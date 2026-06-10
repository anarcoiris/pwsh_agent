"""Crash-loop protection for the agent startup.

Port of NanoClaw v2's circuit-breaker.ts — if the process crashes
repeatedly within a 1-hour window, each subsequent restart is delayed
by an exponentially increasing backoff.  A clean shutdown resets the
counter so the next launch is instant.

State is persisted in ``.pulse/circuit-breaker.json`` (transient data,
survives session resets but not full uninstalls).
"""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

from core.runtime_paths import app_root

logger = logging.getLogger("pwsh_agent.core.circuit_breaker")

_CB_DIR = ".pulse"
_CB_FILE = "circuit-breaker.json"

# Window in seconds: if the previous launch was within this window
# the current launch is counted as a consecutive crash.
RESET_WINDOW_S = 3600  # 1 hour

# Backoff schedule in seconds — index = crash attempt (0-based capped).
# Identical to NanoClaw: [0, 0, 10, 30, 120, 300, 900]
BACKOFF_SCHEDULE_S = [0, 0, 10, 30, 120, 300, 900]


def _cb_path() -> Path:
    return app_root() / _CB_DIR / _CB_FILE


def _read_state() -> dict | None:
    path = _cb_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "attempt" in data and "timestamp" in data:
            return data
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return None


def _write_state(attempt: int, timestamp: float) -> None:
    path = _cb_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"attempt": attempt, "timestamp": timestamp}, indent=2),
        encoding="utf-8",
    )


def _get_delay(attempt: int) -> int:
    """Return the backoff delay in seconds for the given attempt number."""
    idx = min(attempt - 1, len(BACKOFF_SCHEDULE_S) - 1)
    return BACKOFF_SCHEDULE_S[max(0, idx)]


def enforce_startup_backoff() -> int:
    """Block the process if repeated crashes are detected.

    Returns the current attempt number (1 = clean start).
    """
    now = time.time()
    prev = _read_state()

    if prev is None:
        attempt = 1
    else:
        elapsed = now - prev["timestamp"]
        if elapsed < RESET_WINDOW_S:
            attempt = prev["attempt"] + 1
            logger.warning(
                "Previous startup was not a clean shutdown "
                "(attempt=%d, elapsed=%ds)",
                prev["attempt"],
                int(elapsed),
            )
        else:
            attempt = 1
            logger.info(
                "Circuit breaker reset — last startup was over 1h ago "
                "(previous attempt=%d)",
                prev["attempt"],
            )

    _write_state(attempt, now)

    delay = _get_delay(attempt)
    if delay > 0:
        resume_at = time.strftime(
            "%H:%M:%S", time.localtime(now + delay)
        )
        msg = (
            f"⚠ Circuit breaker: crash #{attempt} in <1h — "
            f"waiting {delay}s (resume at {resume_at})"
        )
        print(msg, file=sys.stderr)
        logger.warning(msg)
        time.sleep(delay)
        logger.info("Circuit breaker: backoff complete, resuming startup")

    return attempt


def reset() -> None:
    """Call on clean shutdown to clear the breaker state."""
    try:
        _cb_path().unlink(missing_ok=True)
        logger.info("Circuit breaker reset on clean shutdown")
    except OSError:
        pass
