"""
core/user_checkpoint.py — Blocking user checkpoint with whitelist.

When the agent reaches a decision point (failure, stall, attempt cap, or
execution result), it pauses and asks the user for direction.

The user can respond once, or whitelist "always continue in this case"
so that future triggers of the same type proceed automatically.

Whitelist is persisted to state/checkpoint_whitelist.json per session.
"""

from __future__ import annotations

import json
import logging
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger("pwsh_agent.core.user_checkpoint")


class CheckpointTrigger(str, Enum):
    """Trigger types that can pause execution for user input."""
    NEEDS_READAPTATION  = "needs_readaptation"   # a roadmap step FAILED
    STALL_RECOVERY      = "stall_recovery"        # 3+ non-substantive tools
    ATTEMPT_CAP_REACHED = "attempt_cap_reached"   # step BLOCKED after 8 attempts
    EXEC_RESULT_REVIEW  = "exec_result_review"    # after any code/script execution


class CheckpointDecision(str, Enum):
    """What the user decided at the checkpoint."""
    CONTINUE       = "continue"         # continue the current approach
    CHANGE_CONTEXT = "change_context"   # user will add information
    STOP           = "stop"             # stop / mark as done
    ALWAYS_CONTINUE = "always_continue" # whitelist: skip future triggers of this type


# ──────────────────────────────────────────────────────────────────────────────
# Whitelist persistence
# ──────────────────────────────────────────────────────────────────────────────

_WHITELIST_CACHE: dict[str, dict[str, str]] = {}  # {session_id: {trigger: decision}}


def _whitelist_path(session_id: str, cfg: dict[str, Any]) -> Path:
    raw = cfg.get("checkpoint", {}).get("whitelist_file", "state/checkpoint_whitelist.json")
    p = Path(raw)
    if not p.is_absolute():
        # Make it session-scoped under the state dir
        return Path("state") / "sessions" / session_id / "checkpoint_whitelist.json"
    return p


def load_whitelist(session_id: str, cfg: dict[str, Any]) -> dict[str, str]:
    """Load persisted whitelist for this session."""
    if session_id in _WHITELIST_CACHE:
        return _WHITELIST_CACHE[session_id]
    path = _whitelist_path(session_id, cfg)
    try:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                _WHITELIST_CACHE[session_id] = {str(k): str(v) for k, v in data.items()}
                return _WHITELIST_CACHE[session_id]
    except (OSError, json.JSONDecodeError):
        pass
    _WHITELIST_CACHE[session_id] = {}
    return _WHITELIST_CACHE[session_id]


def save_whitelist(session_id: str, cfg: dict[str, Any], whitelist: dict[str, str]) -> None:
    """Persist updated whitelist for this session."""
    _WHITELIST_CACHE[session_id] = whitelist
    path = _whitelist_path(session_id, cfg)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(whitelist, indent=2), encoding="utf-8")
    except OSError as e:
        logger.warning("Could not persist checkpoint whitelist: %s", e)


def is_whitelisted(
    trigger: CheckpointTrigger,
    session_id: str,
    cfg: dict[str, Any],
) -> bool:
    """Return True if the user has whitelisted 'always continue' for this trigger."""
    whitelist = load_whitelist(session_id, cfg)
    return whitelist.get(trigger.value) == CheckpointDecision.ALWAYS_CONTINUE.value


def whitelist_trigger(
    trigger: CheckpointTrigger,
    session_id: str,
    cfg: dict[str, Any],
) -> None:
    """Persist 'always_continue' for this trigger type."""
    whitelist = load_whitelist(session_id, cfg)
    whitelist[trigger.value] = CheckpointDecision.ALWAYS_CONTINUE.value
    save_whitelist(session_id, cfg, whitelist)


# ──────────────────────────────────────────────────────────────────────────────
# Checkpoint messages (rendered to the user in console.py / agent.py)
# ──────────────────────────────────────────────────────────────────────────────

_TRIGGER_MESSAGES: dict[CheckpointTrigger, str] = {
    CheckpointTrigger.NEEDS_READAPTATION: (
        "[Checkpoint] A plan step failed.\n"
        "{detail}\n\n"
        "How should we continue?\n"
        "  [C] Continue — try another strategy\n"
        "  [I] Add information — give me more context\n"
        "  [X] Stop — mark as complete\n"
        "  [S] Always continue on failure (do not ask again)"
    ),
    CheckpointTrigger.STALL_RECOVERY: (
        "[Checkpoint] The agent appears to be stalling.\n"
        "{detail}\n\n"
        "How should we continue?\n"
        "  [C] Continue — keep going\n"
        "  [I] Add information — give me more context\n"
        "  [X] Stop — close the mission\n"
        "  [S] Always continue on stall (do not ask again)"
    ),
    CheckpointTrigger.ATTEMPT_CAP_REACHED: (
        "[Checkpoint] Attempt cap reached for this step.\n"
        "{detail}\n\n"
        "How should we continue?\n"
        "  [C] Continue — change approach\n"
        "  [I] Add information — give me more context\n"
        "  [X] Stop — leave this step unresolved\n"
        "  [S] Always continue when attempts are exhausted (do not ask again)"
    ),
    CheckpointTrigger.EXEC_RESULT_REVIEW: (
        "[Checkpoint] Execution result available.\n"
        "{detail}\n\n"
        "How should we continue?\n"
        "  [C] Continue — proceed to the next step\n"
        "  [I] Add information — give me more context\n"
        "  [X] Stop — mission complete\n"
        "  [S] Always continue after executions (do not ask again)"
    ),
}


def format_checkpoint_message(
    trigger: CheckpointTrigger,
    detail: str = "",
) -> str:
    """Format the checkpoint prompt shown to the user."""
    template = _TRIGGER_MESSAGES.get(trigger, "[Checkpoint] {detail}")
    return template.format(detail=(detail or "").strip()[:400])


# ──────────────────────────────────────────────────────────────────────────────
# Decision parser
# ──────────────────────────────────────────────────────────────────────────────

_RESPONSE_MAP: dict[str, CheckpointDecision] = {
    "c": CheckpointDecision.CONTINUE,
    "continue": CheckpointDecision.CONTINUE,
    "continuar": CheckpointDecision.CONTINUE,
    "seguir": CheckpointDecision.CONTINUE,
    "i": CheckpointDecision.CHANGE_CONTEXT,
    "info": CheckpointDecision.CHANGE_CONTEXT,
    "información": CheckpointDecision.CHANGE_CONTEXT,
    "informacion": CheckpointDecision.CHANGE_CONTEXT,
    "context": CheckpointDecision.CHANGE_CONTEXT,
    "x": CheckpointDecision.STOP,
    "stop": CheckpointDecision.STOP,
    "terminar": CheckpointDecision.STOP,
    "done": CheckpointDecision.STOP,
    "cerrar": CheckpointDecision.STOP,
    "s": CheckpointDecision.ALWAYS_CONTINUE,
    "always": CheckpointDecision.ALWAYS_CONTINUE,
    "siempre": CheckpointDecision.ALWAYS_CONTINUE,
    "always_continue": CheckpointDecision.ALWAYS_CONTINUE,
}


def parse_user_decision(raw_input: str) -> CheckpointDecision:
    """Parse the user's text response into a CheckpointDecision.

    Returns CONTINUE as the safe default for unrecognised input.
    """
    key = (raw_input or "").strip().lower()
    return _RESPONSE_MAP.get(key, CheckpointDecision.CONTINUE)


# ──────────────────────────────────────────────────────────────────────────────
# CheckpointGate — the main entry point
# ──────────────────────────────────────────────────────────────────────────────

class CheckpointGate:
    """Evaluates whether a checkpoint should fire and handles the decision.

    Usage (in agent.py):
        gate = CheckpointGate(session_id, cfg)
        decision = await gate.maybe_checkpoint(
            trigger=CheckpointTrigger.NEEDS_READAPTATION,
            detail="Step 'crack_hash' failed: file not found",
            ask_user_fn=console.ask_checkpoint,
        )
        if decision == CheckpointDecision.STOP:
            break
        if decision == CheckpointDecision.CHANGE_CONTEXT:
            # wait for next user message
            return
    """

    def __init__(self, session_id: str, cfg: dict[str, Any]):
        self.session_id = session_id
        self.cfg = cfg
        checkpoint_cfg = cfg.get("checkpoint", {})
        self.enabled: bool = bool(checkpoint_cfg.get("enabled", True))
        self.profile: str = str(checkpoint_cfg.get("profile", "interactive")).strip().lower()
        triggers = list(checkpoint_cfg.get("triggers", [t.value for t in CheckpointTrigger]))
        if self.profile == "headless":
            triggers = [
                t for t in triggers
                if t not in (
                    CheckpointTrigger.STALL_RECOVERY.value,
                    CheckpointTrigger.EXEC_RESULT_REVIEW.value,
                )
            ]
        self.active_triggers: frozenset[str] = frozenset(triggers)

    def should_fire(self, trigger: CheckpointTrigger) -> bool:
        """Return True when this trigger should pause execution."""
        if not self.enabled:
            return False
        if trigger.value not in self.active_triggers:
            return False
        if is_whitelisted(trigger, self.session_id, self.cfg):
            return False
        return True

    async def maybe_checkpoint(
        self,
        trigger: CheckpointTrigger,
        detail: str,
        ask_user_fn,  # async callable: (message: str) -> str
    ) -> CheckpointDecision:
        """Fire a checkpoint if warranted and return the user's decision.

        Args:
            trigger:     The trigger type.
            detail:      Context string shown to the user (e.g. step label + error).
            ask_user_fn: An async callable that shows the message and returns
                         the user's raw text response.

        Returns:
            CheckpointDecision — always returns something safe.
        """
        if not self.should_fire(trigger):
            return CheckpointDecision.CONTINUE

        message = format_checkpoint_message(trigger, detail)
        try:
            raw = await ask_user_fn(message)
        except Exception as e:
            logger.warning("Checkpoint ask_user_fn raised: %s", e)
            return CheckpointDecision.CONTINUE

        decision = parse_user_decision(raw)

        if decision == CheckpointDecision.ALWAYS_CONTINUE:
            whitelist_trigger(trigger, self.session_id, self.cfg)
            # Treat as continue for this turn
            return CheckpointDecision.CONTINUE

        return decision
