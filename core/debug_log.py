"""Compact NDJSON debug logger for agent diagnostics."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

# Active debug session. All instrumentation funnels into a SINGLE file
# (debug-{_SESSION_ID}.log) to avoid the confusion of several stale debug-*.log
# files left over from earlier debug sessions.
_SESSION_ID = os.environ.get("DEBUG_SESSION_ID", "d9171b")
_LOG = Path(__file__).resolve().parent.parent / f"debug-{_SESSION_ID}.log"
# Legacy scattered debug_log/debug_log_session calls are OFF by default now
# (set PULSE_DEBUG=1 to re-enable them). They were noisy and wrote to old
# session files. Targeted tracing for the active investigation uses trace()
# below, which is always on.
_DEBUG_ENABLED = os.environ.get("PULSE_DEBUG", "0") == "1"


def trace(location: str, message: str, data: dict, run_id: str = "trace") -> None:
    """Always-on targeted trace for the active debug session.

    Unlike debug_log/debug_log_session (gated by PULSE_DEBUG), this always writes
    to the single active-session log so the current investigation's signal is not
    drowned out by, or split across, legacy instrumentation files.
    """
    # #region agent log
    _write_ndjson(_LOG, _SESSION_ID, location, message, data, "", run_id)
    # #endregion


def debug_log(location: str, message: str, data: dict, hypothesis_id: str = "", run_id: str = "verify") -> None:
    # #region agent log
    if not _DEBUG_ENABLED:
        return
    _write_ndjson(_LOG, _SESSION_ID, location, message, data, hypothesis_id, run_id)
    # #endregion


def debug_log_session(
    session_id: str,
    location: str,
    message: str,
    data: dict,
    hypothesis_id: str = "",
    run_id: str = "pre-fix",
) -> None:
    """Legacy entry point. Funnels into the single active-session log (the passed
    session_id is ignored) and is gated by PULSE_DEBUG."""
    # #region agent log
    if not _DEBUG_ENABLED:
        return
    _write_ndjson(_LOG, _SESSION_ID, location, message, data, hypothesis_id, run_id)
    # #endregion


def _write_ndjson(
    log_path: Path,
    session_id: str,
    location: str,
    message: str,
    data: dict,
    hypothesis_id: str,
    run_id: str,
) -> None:
    try:
        payload = {
            "sessionId": session_id,
            "timestamp": int(time.time() * 1000),
            "location": location,
            "message": message,
            "data": data,
            "hypothesisId": hypothesis_id,
            "runId": run_id,
        }
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, default=str) + "\n")
    except Exception:
        pass


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
    """Log which code path ended a mission/chat turn (always-on for the active
    debug session, since turn-completion behavior is under investigation)."""
    trace(
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
        run_id="completion",
    )
