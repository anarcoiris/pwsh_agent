"""Windows idle-time and night-window helpers for the orchestrator."""

from __future__ import annotations

import sys
from datetime import datetime


def get_idle_time_seconds() -> float:
    """Seconds since last user input (Windows). Returns 0 on non-Windows."""
    if not sys.platform.startswith("win"):
        return 0.0
    import ctypes

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

    lii = LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
        millis = ctypes.windll.kernel32.GetTickCount() - lii.dwTime
        return max(0.0, millis / 1000.0)
    return 0.0


def is_night_time(start_hour: int, end_hour: int) -> bool:
    """True if local hour is inside [start, end), including midnight wrap."""
    h = datetime.now().hour
    if start_hour <= end_hour:
        return start_hour <= h < end_hour
    return h >= start_hour or h < end_hour


def conditions_met(
    *,
    requires_idle_seconds: int | None,
    night_start: int | None,
    night_end: int | None,
    allow_urgent: bool = False,
    urgent_min_priority: int = 90,
    job_priority: int = 50,
) -> tuple[bool, str]:
    """Return (ok, reason) for running a queued job now."""
    if allow_urgent and job_priority >= urgent_min_priority:
        idle = get_idle_time_seconds()
        if idle >= 10.0:
            return True, "urgent priority bypass"
        return False, "active input (<10s)"

    idle = get_idle_time_seconds()
    if idle < 10.0:
        return False, "active input (<10s)"

    in_night = False
    if night_start is not None and night_end is not None:
        in_night = is_night_time(night_start, night_end)

    if requires_idle_seconds is not None and idle >= requires_idle_seconds:
        return True, f"idle {idle/60:.1f}m"

    if in_night:
        return True, "night window"

    if requires_idle_seconds is None and night_start is None:
        return True, "no idle/night requirement"

    if requires_idle_seconds is not None:
        return False, f"idle {idle/60:.1f}m < {requires_idle_seconds/60:.1f}m required"

    return False, "outside night window"
