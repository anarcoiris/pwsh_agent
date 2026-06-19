"""
core/model_dispatch.py — Centralised model dispatch for the multi-phase pipeline.

Pipeline:
  INTAKE   → chat-analyzer (7B)   : user msg → structured IntentSpec
  PLAN     → vibethinker  (3B)    : monologue + roadmap decomposition
  VALIDATE → qwen-coder   (7B)    : roadmap JSON schema / feasibility check
  EXECUTE  → qwen-coder   (7B)    : tool_calls, code, strict syntax
  EVALUATE → vibethinker  (3B)    : exec result → next step / done?

Multi-GPU: each phase routes to a dedicated Ollama endpoint (no unload).
Legacy: single base_url when endpoints are not configured.
"""

from __future__ import annotations

import re as _re
from pathlib import Path as _Path

import os
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore


class TurnPhase(str, Enum):
    """Which phase of the pipeline is currently executing."""
    INTAKE   = "intake"
    PLAN     = "plan"
    VALIDATE = "validate"
    EXECUTE  = "execute"
    EVALUATE = "evaluate"


_DEFAULT_ENDPOINTS = {
    "intake":  "http://localhost:11435",
    "planner": "http://localhost:11434",
    "coder":   "http://localhost:11436",
}

_PHASE_ENDPOINT_KEY = {
    TurnPhase.INTAKE:   "intake",
    TurnPhase.PLAN:     "planner",
    TurnPhase.VALIDATE: "coder",
    TurnPhase.EXECUTE:  "coder",
    TurnPhase.EVALUATE: "planner",
}


def _kernel_root() -> Path | None:
    env = os.environ.get("EXPLORATION_KERNEL", "").strip()
    if env:
        p = Path(env).resolve()
        return p if p.is_dir() else None
    candidate = Path.home() / "Documents" / "Libraries" / "exploration-kernel"
    return candidate if candidate.is_dir() else None


@lru_cache(maxsize=1)
def _kernel_pwsh_agent_block() -> dict[str, Any]:
    if yaml is None:
        return {}
    root = _kernel_root()
    if not root:
        return {}
    path = root / "protocols" / "model_routing.yaml"
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except OSError:
        return {}
    block = data.get("pwsh_agent")
    return block if isinstance(block, dict) else {}


def endpoints_config(cfg: dict[str, Any]) -> dict[str, str]:
    """Return {intake, planner, coder} URLs merged from config and kernel."""
    ollama = cfg.get("ollama", {})
    base = str(ollama.get("base_url", _DEFAULT_ENDPOINTS["coder"])).rstrip("/")
    merged = dict(_DEFAULT_ENDPOINTS)
    merged["coder"] = base

    cfg_eps = ollama.get("endpoints") or {}
    if isinstance(cfg_eps, dict):
        for key in ("intake", "planner", "coder"):
            val = cfg_eps.get(key)
            if val:
                merged[key] = str(val).rstrip("/")

    kernel = _kernel_pwsh_agent_block().get("endpoints") or {}
    if isinstance(kernel, dict):
        for key in ("intake", "planner", "coder"):
            val = kernel.get(key)
            if val and key not in (ollama.get("endpoints") or {}):
                merged[key] = str(val).rstrip("/")

    return merged


def endpoint_for_phase(phase: TurnPhase, cfg: dict[str, Any]) -> str:
    """Return Ollama base URL for the given pipeline phase."""
    eps = endpoints_config(cfg)
    key = _PHASE_ENDPOINT_KEY.get(phase, "coder")
    return eps.get(key, eps["coder"])


def unload_after_call(cfg: dict[str, Any]) -> bool:
    """When False, models stay resident (multi-GPU pinned mode)."""
    ollama = cfg.get("ollama", {})
    if "unload_after_call" in ollama:
        return bool(ollama["unload_after_call"])
    kernel = _kernel_pwsh_agent_block()
    if "unload_after_call" in kernel:
        return bool(kernel["unload_after_call"])
    return False


def model_for_phase(phase: TurnPhase, cfg: dict[str, Any]) -> tuple[str, int]:
    """Return (model_name, num_ctx) for the given pipeline phase."""
    ollama = cfg.get("ollama", {})
    default_model   = ollama.get("default_model",       "qwen2.5-coder:7b-instruct")
    intake_model    = ollama.get("conversational_model", "chat-analyzer")
    planner_model   = ollama.get("planner_model",        "vibethinker:3b")
    num_ctx         = int(ollama.get("num_ctx",          8192))
    num_ctx_planner = int(ollama.get("num_ctx_planner",  16384))

    kernel_phases = _kernel_pwsh_agent_block().get("phases") or {}
    if isinstance(kernel_phases, dict):
        phase_key = phase.value
        kphase = kernel_phases.get(phase_key) or {}
        if isinstance(kphase, dict):
            if kphase.get("model"):
                model_override = str(kphase["model"])
                ctx = int(kphase.get("num_ctx", num_ctx))
                return model_override, ctx

    if phase == TurnPhase.INTAKE:
        return intake_model, num_ctx
    if phase in (TurnPhase.PLAN, TurnPhase.EVALUATE):
        return planner_model, num_ctx_planner
    if phase == TurnPhase.VALIDATE:
        return default_model, num_ctx
    return default_model, num_ctx


def num_predict_for_phase(phase: TurnPhase, cfg: dict[str, Any]) -> int:
    """Return num_predict tokens for the given phase."""
    ollama = cfg.get("ollama", {})
    if phase == TurnPhase.VALIDATE:
        return int(ollama.get("num_predict_validate", 1024))
    if phase in (TurnPhase.PLAN, TurnPhase.EVALUATE):
        return int(ollama.get("num_predict_planner", 2048))
    return int(ollama.get("num_predict", 3072))


def temperature_for_phase(phase: TurnPhase, cfg: dict[str, Any]) -> float:
    """Return temperature for the given phase."""
    if phase == TurnPhase.INTAKE:
        return float(cfg.get("intent", {}).get("temperature", 0.1))
    if phase == TurnPhase.VALIDATE:
        return float(cfg.get("planner", {}).get("validate_temperature", 0.1))
    if phase in (TurnPhase.PLAN, TurnPhase.EVALUATE):
        return float(cfg.get("planner", {}).get("temperature", 0.4))
    return 0.1


def should_reevaluate(trigger: str, cfg: dict[str, Any]) -> bool:
    """Return True when the planner (VT) should re-evaluate after this trigger."""
    planner = cfg.get("planner", {})
    triggers: list[str] = planner.get(
        "reevaluate_on", ["start", "failure", "warning", "exec_result"]
    )
    return trigger in triggers


def chat_options_for_phase(
    phase: TurnPhase,
    cfg: dict[str, Any],
    *,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build Ollama options dict including keep_alive when unload is disabled."""
    model_name, num_ctx = model_for_phase(phase, cfg)
    opts: dict[str, Any] = {
        "temperature": temperature_for_phase(phase, cfg),
        "num_ctx": num_ctx,
        "num_predict": num_predict_for_phase(phase, cfg),
    }
    if not unload_after_call(cfg):
        opts["keep_alive"] = "24h"
    elif extra is None or "keep_alive" not in (extra or {}):
        opts["keep_alive"] = 0
    if extra:
        opts.update(extra)
    return opts


# ──────────────────────────────────────────────────────────────────────────────
# Monologue prompt helpers
# ──────────────────────────────────────────────────────────────────────────────

# Hardcoded fallback strings — used when the .md skill file is not on disk.
# The canonical versions live in knowledge/skills/planner_*.md and are loaded
# at call time by _load_skill_system().

_MONOLOGUE_SYSTEM = (
    "You are VibeThinker, a strategic reasoning model leading an AI agent. "
    "You think out loud in the FIRST PERSON. Write as if you are thinking privately "
    "before committing to a plan. Be specific, critical, and structured. "
    "Do NOT produce tool calls — this is your internal reasoning phase. "
    "Speak naturally, as if you are a focused engineer talking to yourself."
)

_MONOLOGUE_USER_TPL = (
    "Here is what the user wants, formalised by the intake model:\n\n"
    "{intent_summary}\n\n"
    "Domain: {domain}\n"
    "Objectives: {objectives}\n"
    "Targets: {targets}\n"
    "Deliverables: {deliverables}\n"
    "Safety flags: {safety}\n\n"
    "Think through this request. What is the real goal? What are the risks? "
    "What is the best sequence of actions? What could go wrong? "
    "Be honest about what you don't know."
)

_ROADMAP_SYSTEM = (
    "You are VibeThinker, a strategic planning model for an AI agent. "
    "After your reasoning, produce a STRUCTURED ROADMAP as a JSON array of steps. "
    "Each step: {id, label, tool_hint, assigned_agent, success_criteria, rationale, "
    "depends_on (optional array of step ids), parallel_group (optional string)}. "
    "assigned_agent must be one of: lead, workspace, web, recon, forensic, crypto. "
    "tool_hint is the primary tool name (e.g. write_file, run_script, http_get). "
    "Output JSON ONLY — a valid array, no prose, no markdown."
)

_ROADMAP_USER_TPL = (
    "Your internal reasoning:\n{monologue}\n\n"
    "Now produce the roadmap JSON array for this mission:\n{intent_summary}\n"
    "Domain: {domain} | Objectives: {objectives}"
)

_SKILLS_DIR = _Path(__file__).parent.parent / "knowledge" / "skills"
_FRONTMATTER_STRIP_RE = _re.compile(r"^---\s*\n.*?\n---\s*\n", _re.DOTALL)


def _load_skill_system(skill_name: str, fallback: str) -> str:
    """Load system prompt body from knowledge/skills/<skill_name>.md.

    Strips YAML frontmatter. Returns ``fallback`` if the file is missing or
    unreadable — no exceptions propagate to the caller.
    """
    path = _SKILLS_DIR / f"{skill_name}.md"
    try:
        raw = path.read_text(encoding="utf-8")
        body = _FRONTMATTER_STRIP_RE.sub("", raw, count=1).strip()
        return body if body else fallback
    except Exception:
        return fallback


def build_monologue_messages(
    intent_spec: "Any",
    *,
    prior_monologue: str = "",
    exec_result: str = "",
) -> list[dict[str, str]]:
    """Build the messages list for a VibeThinker monologue call."""
    system = _load_skill_system("planner_monologue", _MONOLOGUE_SYSTEM)
    user_content = _MONOLOGUE_USER_TPL.format(
        intent_summary=getattr(intent_spec, "summary", str(intent_spec))[:600],
        domain=getattr(intent_spec, "domain", "general"),
        objectives="; ".join(getattr(intent_spec, "objectives", []))[:300] or "(none yet)",
        targets="; ".join(getattr(intent_spec, "targets", []))[:200] or "(none)",
        deliverables="; ".join(getattr(intent_spec, "deliverables", []))[:200] or "(none)",
        safety=str(getattr(intent_spec, "safety", ""))[:120],
    )
    if exec_result:
        user_content += f"\n\nLatest execution result:\n{exec_result[:600]}"
    if prior_monologue:
        user_content += f"\n\nYour previous reasoning:\n{prior_monologue[:400]}"

    return [
        {"role": "system", "content": system},
        {"role": "user",   "content": user_content},
    ]


def build_roadmap_messages(
    intent_spec: "Any",
    monologue: str,
    *,
    rejection_reason: str = "",
) -> list[dict[str, str]]:
    """Build messages for the VibeThinker roadmap decomposition call."""
    system = _load_skill_system("planner_roadmap", _ROADMAP_SYSTEM)
    user_content = _ROADMAP_USER_TPL.format(
        monologue=monologue[:800],
        intent_summary=getattr(intent_spec, "summary", str(intent_spec))[:400],
        domain=getattr(intent_spec, "domain", "general"),
        objectives="; ".join(getattr(intent_spec, "objectives", []))[:300] or "(none)",
    )
    if rejection_reason:
        user_content += (
            f"\n\nThe coder validator rejected your previous roadmap:\n"
            f"{rejection_reason[:600]}\n"
            "Produce a corrected roadmap JSON array."
        )
    return [
        {"role": "system", "content": system},
        {"role": "user",   "content": user_content},
    ]


_EVALUATION_SYSTEM_FALLBACK = (
    "You are VibeThinker, evaluating the progress of an ongoing mission. "
    "Think in FIRST PERSON. Assess what happened, decide the next step. "
    'Output JSON: {"status": "continue"|"done"|"blocked"|"needs_user", '
    '"next_step_id": "<step_id or null>", "hint": "<one line hint>", '
    '"monologue": "<your private first-person reasoning>"}'
)


def build_evaluation_messages(
    intent_spec: "Any",
    monologue: str,
    exec_result: str,
    roadmap_status: str,
) -> list[dict[str, str]]:
    """Build messages for a VibeThinker evaluation call after execution."""
    system = _load_skill_system("planner_evaluation", _EVALUATION_SYSTEM_FALLBACK)
    user = (
        f"Mission goal: {getattr(intent_spec, 'summary', '')[:300]}\n\n"
        f"Current roadmap:\n{roadmap_status}\n\n"
        f"Execution result:\n{exec_result[:600]}\n\n"
        f"Your previous reasoning:\n{monologue[:400]}\n\n"
        "Evaluate: is the mission progressing? What should happen next?"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user",   "content": user},
    ]
