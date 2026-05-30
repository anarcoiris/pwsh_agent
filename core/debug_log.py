"""Compact NDJSON debug logger for agent diagnostics."""
from __future__ import annotations

import json
import time
from pathlib import Path

_LOG = Path(__file__).resolve().parent.parent / "debug-d14d5b.log"


def debug_log(location: str, message: str, data: dict, hypothesis_id: str = "", run_id: str = "verify") -> None:
    # #region agent log
    try:
        payload = {
            "sessionId": "d14d5b",
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
