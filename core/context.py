"""
core/context.py — Agent context manager with JSON state persistence.

Ported and adapted from MCP_Pentesting/core/agent_context.py +
the ContextCompactor in llm_utils.py.

Changes from father:
- No anyio/Docker dependencies
- State path defaults to state/sessions/{session_id}/agent_{mode}.json
  relative to the project root
- ContextCompactor dump file written to same sessions dir
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("pwsh_agent.core.context")

from core.runtime_paths import workspace_root


class DateTimeEncoder(json.JSONEncoder):
    """Handle datetime objects in tool results."""
    def default(self, o):
        if isinstance(o, datetime):
            return o.isoformat()
        return super().default(o)


# ──────────────────────────────────────────────────────────────────────────────
# Context Compactor
# ──────────────────────────────────────────────────────────────────────────────

class ContextCompactor:
    """
    Replaces old tool-result messages (beyond the last 10) with a compact
    digest, and dumps the full content to a session-specific markdown file.
    """

    @staticmethod
    def compact_old_tool_results(
        messages: list, max_context: int, session_id: str
    ) -> list:
        # Only compact if we're approaching the limit
        if len(messages) < max_context * 0.8:
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
            content   = msg.get("content", "")

            dump_parts.append(f"### Tool: {tool_name}\n```\n{content}\n```\n")

            if "exit_code" in content:
                m = __import__("re").search(r'"exit_code":\s*(\d+)', content)
                ec = m.group(1) if m else "?"
                summaries.append(f"- {tool_name}: exit_code={ec}, {len(content)} chars")
            elif "error" in content.lower():
                summaries.append(
                    f"- {tool_name}: ERROR ({content[:100].replace(chr(10), ' ')})"
                )
            else:
                summaries.append(f"- {tool_name}: OK ({len(content)} chars)")

        # Write dump file
        dump_dir = workspace_root() / ".pulse" / "state" / "sessions" / session_id
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
                f"[TOOL HISTORY DIGEST — {len(summaries)} earlier results compacted → {dump_path}]\n"
                + "\n".join(summaries)
            ),
        }

        new_messages = [m for i, m in enumerate(messages) if i not in old_indices]
        new_messages.insert(2, summary_msg)
        return new_messages


# ──────────────────────────────────────────────────────────────────────────────
# Agent Context Manager
# ──────────────────────────────────────────────────────────────────────────────

class AgentContextManager:
    """
    Manages the agent's message history with disk persistence and
    automatic context trimming.
    """

    def __init__(
        self,
        mode: str = "autonomous",
        session_id: str = "default",
        state_path: str | None = None,
        max_total_context: int = 100,
    ):
        self.session_id = session_id
        self.mode       = mode
        self.max_total_context = max_total_context

        if state_path:
            self.state_path = Path(state_path)
        else:
            self.state_path = (
                workspace_root()
                / ".pulse"
                / "state"
                / "sessions"
                / session_id
                / f"agent_{mode}.json"
            )

        self.messages: list[dict[str, Any]] = []
        self.load_state()

    # ── Persistence ───────────────────────────────────────────────────────

    def save_state(self) -> None:
        """Persist message history to disk."""
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.state_path, "w", encoding="utf-8") as f:
                json.dump(self.messages, f, indent=2, cls=DateTimeEncoder)
        except Exception as e:
            logger.warning("Could not save agent state: %s", e)

    def load_state(self) -> None:
        """Load message history from disk if a state file exists."""
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
        """Wipe history and delete the state file."""
        self.messages = []
        try:
            if self.state_path.exists():
                os.remove(self.state_path)
                logger.info("Cleared history and deleted %s", self.state_path)
        except Exception as e:
            logger.warning("Could not delete state file: %s", e)

    # ── Message accessors ─────────────────────────────────────────────────

    def get_messages(self) -> list[dict[str, Any]]:
        return self.messages

    def set_messages(self, messages: list[dict[str, Any]]) -> None:
        self.messages = messages

    def add_message(self, message: dict[str, Any]) -> None:
        self.messages.append(message)

    def has_system(self) -> bool:
        return bool(self.messages) and self.messages[0].get("role") == "system"

    # ── Context trimming ──────────────────────────────────────────────────

    def trim_context(self) -> None:
        """Compact old tool results and enforce hard message cap."""
        self.messages = ContextCompactor.compact_old_tool_results(
            self.messages, self.max_total_context, self.session_id
        )

        if len(self.messages) > self.max_total_context:
            system  = self.messages[0]
            mission = None
            if len(self.messages) > 1 and self.messages[1].get("role") == "user":
                mission = self.messages[1]

            keep_count = self.max_total_context - (2 if mission else 1)
            recent = self.messages[-keep_count:]
            self.messages = [system] + ([mission] if mission else []) + recent

        logger.debug("Context trimmed to %d messages", len(self.messages))
