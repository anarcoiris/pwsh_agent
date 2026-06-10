"""
core/context.py — Agent context manager with JSON state persistence.

Ported and adapted from MCP_Pentesting/core/agent_context.py +
the ContextCompactor in llm_utils.py.
"""

import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("pwsh_agent.core.context")

from core.runtime_paths import app_root
from core.query_anchor import is_system_directive

DIGEST_PREFIX = "[TOOL HISTORY DIGEST"


def _tool_digest_line(tool_name: str, content: str) -> str:
    """Build a compact digest line preserving artifact pointers when present."""
    text = str(content)
    extras: list[str] = []

    try:
        payload = json.loads(text)
        if isinstance(payload, dict):
            for key in ("artifact_file", "verbose_log_file"):
                val = payload.get(key)
                if val:
                    extras.append(f"{key}={val}")
            analysis = payload.get("analysis")
            if isinstance(analysis, dict):
                vlog = analysis.get("verbose_log_file")
                if vlog and f"verbose_log_file={vlog}" not in extras:
                    extras.append(f"verbose_log_file={vlog}")
            if payload.get("success") is False:
                err = str(payload.get("error", ""))[:100].replace("\n", " ")
                suffix = f", {', '.join(extras)}" if extras else ""
                return f"- {tool_name}: ERROR ({err}){suffix}"
            if "exit_code" in payload:
                ec = payload.get("exit_code", "?")
                suffix = f", {', '.join(extras)}" if extras else ""
                return f"- {tool_name}: exit_code={ec}, {len(text)} chars{suffix}"
    except (json.JSONDecodeError, TypeError):
        pass

    if "exit_code" in text:
        m = re.search(r'"exit_code":\s*(\d+)', text)
        ec = m.group(1) if m else "?"
        suffix = f", {', '.join(extras)}" if extras else ""
        return f"- {tool_name}: exit_code={ec}, {len(text)} chars{suffix}"
    if "error" in text.lower():
        suffix = f", {', '.join(extras)}" if extras else ""
        return f"- {tool_name}: ERROR ({text[:100].replace(chr(10), ' ')}){suffix}"

    for key in ("artifact_file", "verbose_log_file"):
        m = re.search(rf'"{key}"\s*:\s*"([^"]+)"', text)
        if m and f"{key}={m.group(1)}" not in extras:
            extras.append(f"{key}={m.group(1)}")

    suffix = f", {', '.join(extras)}" if extras else ""
    return f"- {tool_name}: OK ({len(text)} chars){suffix}"


def estimate_chars(messages: list[dict[str, Any]]) -> int:
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if content is None:
            continue
        total += len(str(content))
        if msg.get("tool_calls"):
            total += len(json.dumps(msg["tool_calls"], default=str))
    return total


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """Lightweight token estimate (~4 chars/token heuristic)."""
    chars = estimate_chars(messages)
    return max(1, chars // 4)


_HEAVY_ARTIFACT_TOOLS = frozenset({
    "read_file",
    "http_get",
    "host_exec",
    "capture_packets",
    "run_script",
})
_OLD_ARTIFACT_CAP = 1000
_OLD_ARTIFACT_SUFFIX = "\n[... aggressively truncated older artifact ...]"


def _cap_message_content(
    msg: dict[str, Any],
    max_chars: int,
    tool_cap: int,
    *,
    truncate_suffix: str | None = None,
) -> dict[str, Any]:
    """Return a copy with content truncated if needed."""
    out = dict(msg)
    content = out.get("content", "")
    if content is None:
        return out
    text = str(content)
    limit = max_chars
    if msg.get("role") == "tool":
        name = msg.get("name", "")
        limit = tool_cap if name == "analyze_pcapng" else max_chars
    if len(text) > limit:
        suffix = truncate_suffix or f"\n[... truncated to {limit} chars]"
        out["content"] = text[:limit] + suffix
    return out


class DateTimeEncoder(json.JSONEncoder):
    """Handle datetime objects in tool results."""
    def default(self, o):
        if isinstance(o, datetime):
            return o.isoformat()
        return super().default(o)


class ContextCompactor:
    """
    Replaces old tool-result messages (beyond the last 10) with a compact
    digest, and dumps the full content to a session-specific markdown file.
    """

    @staticmethod
    def compact_old_tool_results(
        messages: list,
        max_context_chars: int,
        session_id: str,
        max_messages: int = 80,
    ) -> list:
        if estimate_chars(messages) < max_context_chars * 0.75:
            if len(messages) < max_messages * 0.8:
                return messages

        tool_indices = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
        if len(tool_indices) <= 10:
            return messages

        old_indices = set(tool_indices[:-10])
        summaries: list[str] = []
        dump_parts: list[str] = []

        for idx in old_indices:
            msg = messages[idx]
            tool_name = msg.get("name", "unknown")
            content = msg.get("content", "")

            dump_parts.append(f"### Tool: {tool_name}\n```\n{content}\n```\n")
            summaries.append(_tool_digest_line(tool_name, content))

        dump_dir = app_root() / "state" / "sessions" / session_id
        dump_dir.mkdir(parents=True, exist_ok=True)
        dump_path = dump_dir / "context_dump.md"
        try:
            with open(dump_path, "a", encoding="utf-8") as f:
                f.write("\n".join(dump_parts) + "\n")
        except Exception as e:
            logger.warning("Could not write context dump: %s", e)

        summary_msg = {
            "role": "assistant",
            "content": (
                f"{DIGEST_PREFIX} — {len(summaries)} earlier results compacted → {dump_path}]\n"
                + "\n".join(summaries)
            ),
        }

        new_messages = [m for i, m in enumerate(messages) if i not in old_indices]
        digest_idx = next(
            (i for i, m in enumerate(new_messages) if str(m.get("content", "")).startswith(DIGEST_PREFIX)),
            None,
        )
        insert_at = 0
        for i, m in enumerate(new_messages):
            if m.get("role") == "system":
                insert_at = i + 1
            elif m.get("role") == "user" and not is_system_directive(str(m.get("content", ""))):
                insert_at = i + 1
                break
            elif m.get("role") == "user":
                insert_at = i + 1
        if digest_idx is not None:
            new_messages[digest_idx] = summary_msg
        else:
            new_messages.insert(insert_at, summary_msg)
        return new_messages


def _pin_headers(messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split pinned prefix (system + anchor user) from the rest."""
    pinned: list[dict[str, Any]] = []
    rest_start = 0
    i = 0
    while i < len(messages) and messages[i].get("role") == "system":
        pinned.append(messages[i])
        i += 1
    rest_start = i
    if i < len(messages) and messages[i].get("role") == "user":
        if not is_system_directive(str(messages[i].get("content", ""))):
            pinned.append(messages[i])
            rest_start = i + 1
    return pinned, messages[rest_start:]


def _partition_turns(tail: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Group tail into turns: assistant (+tools) + optional user nudges until next assistant."""
    turns: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    i = 0
    while i < len(tail):
        msg = tail[i]
        role = msg.get("role")
        if role == "assistant":
            if current and current[0].get("role") == "assistant":
                turns.append(current)
                current = []
            current.append(msg)
            i += 1
            while i < len(tail) and tail[i].get("role") == "tool":
                current.append(tail[i])
                i += 1
            while i < len(tail) and tail[i].get("role") == "user":
                current.append(tail[i])
                i += 1
            turns.append(current)
            current = []
            continue
        if role == "tool":
            if not current:
                current.append(msg)
            else:
                current.append(msg)
            i += 1
            continue
        if role == "user":
            if current:
                current.append(msg)
            else:
                turns.append([msg])
            i += 1
            continue
        current.append(msg)
        i += 1
    if current:
        turns.append(current)
    return turns


def _collapse_stale_nudges(
    messages: list[dict[str, Any]],
    keep_recent: int = 2,
) -> list[dict[str, Any]]:
    """Drop superseded control nudges, keeping only the most recent few.

    Recurrent system directives (goal/deliverable/stall/readaptation) are
    transient: only the latest one or two matter for the next decision. Older
    copies are pure context tax, so we collapse them here. The pinned anchor
    user message is never a directive (is_system_directive == False), so it is
    preserved.
    """
    nudge_idx = [
        i
        for i, m in enumerate(messages)
        if m.get("role") == "user" and is_system_directive(str(m.get("content", "")))
    ]
    if len(nudge_idx) <= keep_recent:
        return messages
    drop = set(nudge_idx[:-keep_recent])
    return [m for i, m in enumerate(messages) if i not in drop]


def _drop_oldest_turns(
    pinned: list[dict[str, Any]],
    turns: list[list[dict[str, Any]]],
    max_chars: int,
) -> list[dict[str, Any]]:
    while turns and estimate_chars(pinned + [m for t in turns for m in t]) > max_chars:
        turns.pop(0)
    flat = pinned + [m for t in turns for m in t]
    return flat


def _apply_per_message_caps(
    messages: list[dict[str, Any]],
    max_tool_chars: int,
    per_message_cap: int | None = None,
) -> list[dict[str, Any]]:
    cap = per_message_cap or max_tool_chars
    pinned, tail = _pin_headers(messages)
    turns = _partition_turns(tail)
    if not turns:
        out: list[dict[str, Any]] = []
        for msg in messages:
            if msg.get("role") == "system":
                out.append(msg)
                continue
            if msg.get("role") == "user" and is_system_directive(str(msg.get("content", ""))):
                out.append(msg)
                continue
            out.append(_cap_message_content(msg, cap, max_tool_chars))
        return out

    out = list(pinned)
    for ti, turn in enumerate(turns):
        is_recent = ti == len(turns) - 1
        for msg in turn:
            if msg.get("role") == "system":
                out.append(msg)
                continue
            if msg.get("role") == "user" and is_system_directive(str(msg.get("content", ""))):
                out.append(msg)
                continue
            if (
                not is_recent
                and msg.get("role") == "tool"
                and msg.get("name", "") in _HEAVY_ARTIFACT_TOOLS
            ):
                out.append(
                    _cap_message_content(
                        msg,
                        _OLD_ARTIFACT_CAP,
                        max_tool_chars,
                        truncate_suffix=_OLD_ARTIFACT_SUFFIX,
                    )
                )
            else:
                out.append(_cap_message_content(msg, cap, max_tool_chars))
    return out


class AgentContextManager:
    """
    Manages the agent's message history with disk persistence (SQLite or JSON)
    and automatic context trimming.
    """

    def __init__(
        self,
        mode: str = "autonomous",
        session_id: str = "default",
        state_path: str | None = None,
        max_total_context: int = 80,
        max_context_chars: int = 47_000,
        max_tool_result_chars: int = 22_000,
        max_context_tokens: int = 0,
        reserve_generation_tokens: int = 3072,
        reserve_injection_tokens: int = 2048,
    ):
        self.session_id = session_id
        self.mode = mode
        self.max_total_context = max_total_context
        self.max_context_chars = max_context_chars
        self.max_tool_result_chars = max_tool_result_chars
        self.max_context_tokens = max(0, int(max_context_tokens or 0))
        self.reserve_generation_tokens = max(0, int(reserve_generation_tokens))
        self.reserve_injection_tokens = max(0, int(reserve_injection_tokens))

        self.use_sqlite = False
        self.db = None

        if state_path:
            self.state_path = Path(state_path)
        else:
            self.state_path = (
                app_root()
                / "state"
                / "sessions"
                / session_id
                / f"agent_{mode}.json"
            )
            self.use_sqlite = True
            from core.session_paths import session_state_dir, facts_file, plan_state_file, intent_spec_file
            from core.working_state import _working_memory_path, current_state_file
            from core.session_handoff import handoff_file
            from core.session_db import SessionDB

            db_path = session_state_dir(self.session_id) / "session.db"
            if not db_path.is_file():
                # Perform auto-migration if there is any legacy JSON data
                has_json = self.state_path.is_file()
                has_others = any(
                    p.is_file() for p in (
                        facts_file(self.session_id),
                        plan_state_file(self.session_id),
                        intent_spec_file(self.session_id),
                        _working_memory_path(self.session_id),
                        handoff_file(self.session_id),
                    )
                )
                if has_json or has_others:
                    logger.info("Auto-migrating session %s to SQLite...", self.session_id)
                    self.db = SessionDB(self.session_id)
                    
                    # 1. Messages
                    if self.state_path.is_file():
                        try:
                            with open(self.state_path, encoding="utf-8") as f:
                                msgs = json.load(f)
                            for msg in msgs:
                                self.db.add_message(msg)
                            logger.info("Migrated %d messages", len(msgs))
                        except Exception as e:
                            logger.warning("Failed to migrate messages: %s", e)

                    # 2. Key-value state files
                    state_mappings = {
                        "facts": facts_file(self.session_id),
                        "plan_state": plan_state_file(self.session_id),
                        "intent_spec": intent_spec_file(self.session_id),
                        "working_memory": _working_memory_path(self.session_id),
                        "handoff": handoff_file(self.session_id),
                    }
                    for key, path in state_mappings.items():
                        if path.is_file():
                            try:
                                val = json.loads(path.read_text(encoding="utf-8"))
                                self.db.set_state(key, val)
                                logger.info("Migrated key '%s'", key)
                            except Exception as e:
                                logger.warning("Failed to migrate %s: %s", key, e)

                    # 3. CURRENT_STATE.md
                    cs_file = current_state_file(self.session_id)
                    if cs_file.is_file():
                        try:
                            val = cs_file.read_text(encoding="utf-8")
                            self.db.set_state("current_state_md", val)
                            logger.info("Migrated current_state_md")
                        except Exception as e:
                            logger.warning("Failed to migrate current_state_md: %s", e)

                    # 4. Rename original files to .bak
                    all_to_rename = list(state_mappings.values()) + [self.state_path, cs_file]
                    for path in all_to_rename:
                        if path.is_file():
                            try:
                                path.rename(path.with_suffix(path.suffix + ".bak"))
                            except Exception as e:
                                logger.warning("Failed to rename %s: %s", path, e)
                    logger.info("Migration of session %s complete.", self.session_id)
                else:
                    self.db = SessionDB(self.session_id)
            else:
                self.db = SessionDB(self.session_id)

        self.messages: list[dict[str, Any]] = []
        self.load_state()

    def save_state(self) -> None:
        if self.use_sqlite:
            try:
                self.db._conn.execute("DELETE FROM messages")
                self.db._conn.execute("DELETE FROM sqlite_sequence WHERE name='messages'")
                for msg in self.messages:
                    self.db.add_message(msg)
                # Reload to populate seq attribute in memory
                self.messages = self.db.get_messages()
            except Exception as e:
                logger.warning("Could not save agent state to SQLite: %s", e)
        else:
            try:
                self.state_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.state_path, "w", encoding="utf-8") as f:
                    json.dump(self.messages, f, indent=2, cls=DateTimeEncoder)
            except Exception as e:
                logger.warning("Could not save agent state: %s", e)

    def load_state(self) -> None:
        if self.use_sqlite:
            try:
                self.messages = self.db.get_messages()
                logger.info(
                    "Loaded %d messages from SQLite session.db", len(self.messages)
                )
            except Exception as e:
                logger.warning("Could not load agent state from SQLite: %s", e)
        else:
            try:
                if self.state_path.exists():
                    with open(self.state_path, encoding="utf-8") as f:
                        self.messages = json.load(f)
                    logger.info(
                        "Loaded %d messages from %s", len(self.messages), self.state_path
                    )
            except Exception as e:
                logger.warning("Could not load agent state: %s", e)

    def clear_history(self) -> None:
        self.messages = []
        if self.use_sqlite:
            try:
                from core.session_paths import session_state_dir
                self.db.close()
                db_path = session_state_dir(self.session_id) / "session.db"
                for suffix in ("", "-wal", "-shm"):
                    p = db_path.with_name(db_path.name + suffix)
                    if p.is_file():
                        p.unlink()
                # Also unlink any .bak files if they exist
                for p in session_state_dir(self.session_id).iterdir():
                    if p.suffix == ".bak" or p.name.endswith(".json.bak") or p.name.endswith(".md.bak"):
                        p.unlink()
                logger.info("Cleared SQLite history and deleted session.db files")
                # Re-initialize SessionDB
                from core.session_db import SessionDB
                self.db = SessionDB(self.session_id)
            except Exception as e:
                logger.warning("Could not clear SQLite history: %s", e)
        else:
            try:
                if self.state_path.exists():
                    os.remove(self.state_path)
                    logger.info("Cleared history and deleted %s", self.state_path)
            except Exception as e:
                logger.warning("Could not delete state file: %s", e)

    def get_messages(self) -> list[dict[str, Any]]:
        return self.messages

    def messages_for_llm(self, max_turns: int | None = None) -> list[dict[str, Any]]:
        """Return a windowed view for the LLM: pinned headers + last N turns.

        Phase 2 history policy: the full log stays in self.messages (and on disk
        via save_state) for audit/replay, but the model only needs the pinned
        system/anchor prefix plus the most recent turns — CURRENT STATE carries
        the durable continuity. Non-mutating: never shrinks the canonical log.
        """
        msgs = self.messages
        if not max_turns or max_turns <= 0:
            return msgs
        pinned, tail = _pin_headers(msgs)
        turns = _partition_turns(tail)
        if len(turns) <= max_turns:
            return msgs
        kept = turns[-max_turns:]
        return pinned + [m for t in kept for m in t]

    def set_messages(self, messages: list[dict[str, Any]]) -> None:
        self.messages = messages

    def add_message(self, message: dict[str, Any]) -> None:
        self.messages.append(message)

    def has_system(self) -> bool:
        return bool(self.messages) and self.messages[0].get("role") == "system"

    def trim_context(self) -> None:
        """Compact old tool results and enforce budget-first trim policy."""
        # Collapse superseded control nudges before any other trimming so they
        # do not consume the char/token budget reserved for real signal.
        self.messages = _collapse_stale_nudges(self.messages, keep_recent=2)
        self.messages = ContextCompactor.compact_old_tool_results(
            self.messages,
            self.max_context_chars,
            self.session_id,
            max_messages=self.max_total_context,
        )

        pinned, tail = _pin_headers(self.messages)
        turns = _partition_turns(tail)
        self.messages = _drop_oldest_turns(pinned, turns, self.max_context_chars)

        # Token-budget trim (primary when configured)
        if self.max_context_tokens > 0:
            hard_budget = max(
                256,
                self.max_context_tokens
                - self.reserve_generation_tokens
                - self.reserve_injection_tokens,
            )
            pinned2, tail2 = _pin_headers(self.messages)
            turns2 = _partition_turns(tail2)
            while turns2 and estimate_tokens(pinned2 + [m for t in turns2 for m in t]) > hard_budget:
                turns2.pop(0)
            self.messages = pinned2 + [m for t in turns2 for m in t]

        # Message cap is fallback safety, not primary policy.
        if len(self.messages) > self.max_total_context:
            pinned, tail = _pin_headers(self.messages)
            turns = _partition_turns(tail)
            keep_turns = max(1, self.max_total_context - len(pinned))
            while len(turns) > keep_turns:
                turns.pop(0)
            self.messages = pinned + [m for t in turns for m in t]

        self.messages = _apply_per_message_caps(
            self.messages,
            self.max_tool_result_chars,
            per_message_cap=min(12_000, self.max_tool_result_chars),
        )

        logger.debug(
            "Context trimmed to %d messages, ~%d chars",
            len(self.messages),
            estimate_chars(self.messages),
        )
        try:
            from core.debug_log import debug_log

            debug_log(
                "context.trim_context",
                "trim complete",
                {
                    "message_count": len(self.messages),
                    "estimated_chars": estimate_chars(self.messages),
                    "estimated_tokens": estimate_tokens(self.messages),
                    "max_context_tokens": self.max_context_tokens,
                },
                "CTX",
            )
        except Exception:
            pass
