"""SQLite-backed mission scheduler with cron support.

Inspired by NanoClaw v2's ``messages_in.process_after`` +
``recurrence`` pattern.  Allows one-shot and recurring autonomous
missions without external cron or Windows Task Scheduler.

The database lives at ``.pulse/scheduler.db`` — separate from session
data so scheduled missions survive session resets.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from croniter import croniter

from core.runtime_paths import app_root

logger = logging.getLogger("pwsh_agent.core.scheduler")

_DB_DIR = ".pulse"
_DB_FILE = "scheduler.db"

_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS scheduled_missions (
    id           TEXT    PRIMARY KEY,
    mission_text TEXT    NOT NULL,
    cron_expr    TEXT,                     -- NULL = one-shot
    next_run_at  TEXT    NOT NULL,         -- ISO-8601 UTC
    last_run_at  TEXT,                     -- ISO-8601 UTC
    status       TEXT    DEFAULT 'active', -- active | paused | done | failed
    max_runs     INTEGER,                 -- NULL = unlimited
    run_count    INTEGER DEFAULT 0,
    created_at   TEXT    NOT NULL,
    specialist   TEXT    DEFAULT 'lead',
    network_mode TEXT    DEFAULT 'SANDBOX',
    last_error   TEXT
);

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);

INSERT OR IGNORE INTO schema_version (version) VALUES (1);
"""


def _db_path() -> Path:
    return app_root() / _DB_DIR / _DB_FILE


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA_SQL)
    return conn


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _next_run_from_cron(cron_expr: str, base: datetime | None = None) -> str:
    """Compute the next run time from a cron expression."""
    base = base or datetime.now(timezone.utc)
    it = croniter(cron_expr, base)
    return datetime.fromtimestamp(it.get_next(float), tz=timezone.utc).isoformat()


def validate_cron(expr: str) -> bool:
    """Return True if *expr* is a valid 5-field cron expression."""
    try:
        croniter(expr)
        return True
    except (ValueError, KeyError, TypeError):
        return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def schedule_mission(
    mission_text: str,
    *,
    cron_expr: str | None = None,
    run_at: str | None = None,
    specialist: str = "lead",
    network_mode: str = "SANDBOX",
    max_runs: int | None = None,
) -> str:
    """Schedule a new mission.  Returns its id.

    Parameters
    ----------
    mission_text : str
        The prompt to feed to ``run_mission()``.
    cron_expr : str, optional
        Standard 5-field cron expression for recurring missions.
    run_at : str, optional
        ISO-8601 UTC timestamp for one-shot missions.  Ignored if
        *cron_expr* is given.  Defaults to "now" (immediate).
    specialist : str
        Which specialist persona to activate (default ``lead``).
    network_mode : str
        ``SANDBOX`` or ``HOST``.
    max_runs : int, optional
        Cap on total executions (``None`` = unlimited for recurring,
        1 for one-shot).
    """
    mission_id = uuid.uuid4().hex[:12]
    now = _now_iso()

    if cron_expr:
        if not validate_cron(cron_expr):
            raise ValueError(f"Invalid cron expression: {cron_expr!r}")
        next_run = _next_run_from_cron(cron_expr)
    elif run_at:
        next_run = run_at
    else:
        next_run = now  # immediate

    if max_runs is None and not cron_expr:
        max_runs = 1  # one-shot default

    conn = _connect()
    try:
        conn.execute(
            """INSERT INTO scheduled_missions
               (id, mission_text, cron_expr, next_run_at, status,
                max_runs, created_at, specialist, network_mode)
               VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?)""",
            (mission_id, mission_text, cron_expr, next_run,
             max_runs, now, specialist, network_mode),
        )
        conn.commit()
    finally:
        conn.close()

    logger.info(
        "Mission scheduled: id=%s cron=%s next=%s specialist=%s",
        mission_id, cron_expr, next_run, specialist,
    )
    return mission_id


def list_scheduled(*, include_done: bool = False) -> list[dict[str, Any]]:
    """Return all scheduled missions as dicts."""
    conn = _connect()
    try:
        if include_done:
            rows = conn.execute(
                "SELECT * FROM scheduled_missions ORDER BY next_run_at"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM scheduled_missions WHERE status IN ('active', 'paused') "
                "ORDER BY next_run_at"
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_due_missions() -> list[dict[str, Any]]:
    """Return missions where ``next_run_at <= now`` and ``status = 'active'``."""
    now = _now_iso()
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM scheduled_missions "
            "WHERE status = 'active' AND next_run_at <= ? "
            "ORDER BY next_run_at",
            (now,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def mark_completed(mission_id: str) -> None:
    """Mark a mission execution as completed; schedule next run if recurring."""
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM scheduled_missions WHERE id = ?",
            (mission_id,),
        ).fetchone()
        if not row:
            return

        now = _now_iso()
        new_count = (row["run_count"] or 0) + 1

        if row["cron_expr"]:
            # Check if max_runs reached
            if row["max_runs"] is not None and new_count >= row["max_runs"]:
                conn.execute(
                    "UPDATE scheduled_missions SET status='done', "
                    "run_count=?, last_run_at=?, last_error=NULL WHERE id=?",
                    (new_count, now, mission_id),
                )
            else:
                next_run = _next_run_from_cron(row["cron_expr"])
                conn.execute(
                    "UPDATE scheduled_missions SET run_count=?, "
                    "last_run_at=?, next_run_at=?, last_error=NULL WHERE id=?",
                    (new_count, now, next_run, mission_id),
                )
        else:
            # One-shot → done
            conn.execute(
                "UPDATE scheduled_missions SET status='done', "
                "run_count=?, last_run_at=?, last_error=NULL WHERE id=?",
                (new_count, now, mission_id),
            )
        conn.commit()
    finally:
        conn.close()


def mark_failed(mission_id: str, error: str) -> None:
    """Record an execution failure without changing status (stays active for retry)."""
    conn = _connect()
    try:
        conn.execute(
            "UPDATE scheduled_missions SET last_error=?, last_run_at=? WHERE id=?",
            (error[:2000], _now_iso(), mission_id),
        )
        conn.commit()
    finally:
        conn.close()


def pause_mission(mission_id: str) -> bool:
    """Pause a scheduled mission.  Returns True if updated."""
    conn = _connect()
    try:
        cur = conn.execute(
            "UPDATE scheduled_missions SET status='paused' "
            "WHERE id=? AND status='active'",
            (mission_id,),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def resume_mission(mission_id: str) -> bool:
    """Resume a paused mission.  Returns True if updated."""
    conn = _connect()
    try:
        cur = conn.execute(
            "UPDATE scheduled_missions SET status='active' "
            "WHERE id=? AND status='paused'",
            (mission_id,),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def cancel_mission(mission_id: str) -> bool:
    """Cancel (delete) a scheduled mission.  Returns True if deleted."""
    conn = _connect()
    try:
        cur = conn.execute(
            "DELETE FROM scheduled_missions WHERE id=?",
            (mission_id,),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()
