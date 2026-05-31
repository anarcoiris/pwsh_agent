"""Compact NDJSON debug logger for agent diagnostics."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

_SESSION_ID = os.environ.get("DEBUG_SESSION_ID", "d21bac")
_LOG = Path(__file__).resolve().parent.parent / f"debug-{_SESSION_ID}.log"
_DEBUG_ENABLED = os.environ.get("PULSE_DEBUG", "1") == "1"


def debug_log(location: str, message: str, data: dict, hypothesis_id: str = "", run_id: str = "verify") -> None:
    # #region agent log
    if not _DEBUG_ENABLED:
        return
    try:
        payload = {
            "sessionId": _SESSION_ID,
            "timestamp": int(time.time() * 1000),
            "location": location,
            "message": message,
            "data": data,
            "hypothesisId": hypothesis_id,
            "runId": run_id,
        }
        with open(_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, default=str) + "\n")
    except Exception:
        pass
    # #endregion


def log_completion_exit(
    mode: str,
    reason: str,
    *,
    step: int = 0,
    tools_executed: int = 0,
    objective_ok: bool | None = None,
    chat_goals_label: str = "",
    pending_goals: list[str] | None = None,
    hypothesis_id: str = "exit",
) -> None:
    """Log which code path ended a mission/chat turn (debug session d63d0c)."""
    debug_log(
        "agent.completion",
        reason,
        {
            "mode": mode,
            "step": step,
            "tools_executed": tools_executed,
            "objective_ok": objective_ok,
            "chat_goals_label": chat_goals_label,
            "pending_goals": pending_goals or [],
        },
        hypothesis_id=hypothesis_id,
        run_id="completion",
    )
