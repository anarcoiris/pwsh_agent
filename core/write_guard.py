"""
core/write_guard.py — Redirect or block misrouted write_file calls.
"""

from __future__ import annotations

import logging
from typing import Any

from core.task_intent import TaskIntent, TaskIntentExtractor, path_matches_deliverable

logger = logging.getLogger("pwsh_agent.core.write_guard")


class WriteGuard:
    """
    Apply deliverable vs workspace-note rules before tool dispatch.

    Returns (tool_name, args, error_message).
    error_message set → caller should record tool error without executing.
    """

    @classmethod
    def apply(
        cls,
        tool_name: str,
        args: dict[str, Any],
        intent: TaskIntent | None,
        session_id: str = "default",
        pending_deliverables: list[str] | None = None,
    ) -> tuple[str, dict[str, Any], str | None]:
        if tool_name != "write_file":
            return tool_name, args, None

        path = str(args.get("path", "")).replace("\\", "/")
        content = str(args.get("content", ""))

        # Distinguish "not provided" (None) from "explicitly empty" ([]): an
        # empty list means the caller asserts there are no pending deliverables
        # and must not silently fall back to the intent-derived list.
        if pending_deliverables is not None:
            pending = pending_deliverables
        else:
            pending = intent.pending_deliverables() if intent else []

        if pending and not path_matches_deliverable(path, pending):
            # #region agent log
            try:
                from core.debug_log import debug_log
                debug_log(
                    "write_guard.py:apply",
                    "blocked wrong deliverable path",
                    {"path": path, "pending": pending},
                    "W1",
                )
            except Exception:
                pass
            # #endregion
            return tool_name, args, (
                f"Blocked: required deliverable is '{pending[0]}', not '{path}'. "
                f"Call write_file(path='{pending[0]}', content=<REAL extracted values>)."
            )

        if not TaskIntentExtractor.is_workspace_meta_path(path):
            return tool_name, args, None

        if intent and intent.is_dev_task and pending:
            if TaskIntentExtractor.is_progress_note(content):
                hint = pending[0]
                return tool_name, args, (
                    f"Blocked: use append_note for progress notes. "
                    f"Deliverable not yet on disk: {hint}. "
                    f"Call write_file with path '{hint}' and the full script content first."
                )

        if TaskIntentExtractor.is_progress_note(content):
            line = content.strip().splitlines()[0] if content.strip() else content
            logger.info("WriteGuard: redirecting write_file(%s) → append_note", path)
            return "append_note", {"path": path, "line": line, "session_id": session_id}, None

        return tool_name, args, None
