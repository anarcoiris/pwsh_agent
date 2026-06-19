"""Unified work queue for pwsh_agent, hygiene, editorial, and custom jobs."""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from croniter import croniter

from core.runtime_paths import app_root

logger = logging.getLogger("pwsh_agent.core.work_queue")

_DB_DIR = ".pulse"
_DB_FILE = "work_queue.db"

JOB_TYPES = frozenset({
    "pwsh_mission",
    "hygiene_review",
    "hygiene_scan",
    "editorial",
    "custom",
})

_STATUSES_ACTIVE = ("pending", "running", "paused")

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS work_jobs (
    id                  TEXT PRIMARY KEY,
    job_type            TEXT NOT NULL,
    title               TEXT NOT NULL DEFAULT '',
    payload_json        TEXT NOT NULL,
    priority            INTEGER NOT NULL DEFAULT 50,
    status              TEXT NOT NULL DEFAULT 'pending',
    created_at          TEXT NOT NULL,
    scheduled_at        TEXT NOT NULL,
    cron_expr           TEXT,
    next_run_at         TEXT NOT NULL,
    run_count           INTEGER NOT NULL DEFAULT 0,
    max_runs            INTEGER,
    last_run_at         TEXT,
    last_error          TEXT,
    requires_idle_seconds INTEGER,
    night_start_hour    INTEGER,
    night_end_hour      INTEGER,
    requires_gpu        TEXT DEFAULT 'any',
    checkpoint_profile  TEXT DEFAULT 'mvf_autonomous'
);

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);
INSERT OR IGNORE INTO schema_version (version) VALUES (1);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db_path():
    return app_root() / _DB_DIR / _DB_FILE


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    return conn


def _next_cron(cron_expr: str, base: datetime | None = None) -> str:
    base = base or datetime.now(timezone.utc)
    it = croniter(cron_expr, base)
    return datetime.fromtimestamp(it.get_next(float), tz=timezone.utc).isoformat()


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    try:
        d["payload"] = json.loads(d.pop("payload_json") or "{}")
    except json.JSONDecodeError:
        d["payload"] = {}
    return d


def enqueue_job(
    job_type: str,
    payload: dict[str, Any],
    *,
    title: str = "",
    priority: int = 50,
    run_at: str | None = None,
    cron_expr: str | None = None,
    max_runs: int | None = None,
    requires_idle_seconds: int | None = None,
    night_start_hour: int | None = None,
    night_end_hour: int | None = None,
    requires_gpu: str = "any",
    checkpoint_profile: str = "mvf_autonomous",
) -> str:
    """Add a job to the unified queue. Returns job id."""
    if job_type not in JOB_TYPES:
        raise ValueError(f"Unknown job_type: {job_type!r}")

    job_id = uuid.uuid4().hex[:12]
    now = _now_iso()
    if cron_expr:
        next_run = _next_cron(cron_expr)
    elif run_at:
        next_run = run_at
    else:
        next_run = now

    if max_runs is None and not cron_expr:
        max_runs = 1

    if not title:
        title = _default_title(job_type, payload)

    conn = _connect()
    try:
        conn.execute(
            """INSERT INTO work_jobs
               (id, job_type, title, payload_json, priority, status, created_at,
                scheduled_at, cron_expr, next_run_at, max_runs,
                requires_idle_seconds, night_start_hour, night_end_hour,
                requires_gpu, checkpoint_profile)
               VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                job_id,
                job_type,
                title[:200],
                json.dumps(payload, ensure_ascii=False),
                int(priority),
                now,
                run_at or now,
                cron_expr,
                next_run,
                max_runs,
                requires_idle_seconds,
                night_start_hour,
                night_end_hour,
                requires_gpu,
                checkpoint_profile,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    logger.info("Enqueued job %s type=%s priority=%s", job_id, job_type, priority)
    return job_id


def _default_title(job_type: str, payload: dict[str, Any]) -> str:
    if job_type == "pwsh_mission":
        return (payload.get("mission_text") or "mission")[:80]
    if job_type in ("hygiene_review", "hygiene_scan"):
        return payload.get("repo_path", "hygiene")[-80:]
    return job_type


def list_jobs(*, include_done: bool = False, limit: int = 100) -> list[dict[str, Any]]:
    conn = _connect()
    try:
        if include_done:
            rows = conn.execute(
                "SELECT * FROM work_jobs ORDER BY priority DESC, next_run_at LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM work_jobs WHERE status IN ('pending','running','paused') "
                "ORDER BY priority DESC, next_run_at LIMIT ?",
                (limit,),
            ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def get_runnable_jobs(now_iso: str | None = None) -> list[dict[str, Any]]:
    """Jobs due by time, pending, ordered by priority desc."""
    now = now_iso or _now_iso()
    conn = _connect()
    try:
        rows = conn.execute(
            """SELECT * FROM work_jobs
               WHERE status = 'pending' AND next_run_at <= ?
               ORDER BY priority DESC, next_run_at""",
            (now,),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def mark_job_running(job_id: str) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE work_jobs SET status='running', last_run_at=? WHERE id=?",
            (_now_iso(), job_id),
        )
        conn.commit()
    finally:
        conn.close()


def mark_job_completed(job_id: str) -> None:
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM work_jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            return
        now = _now_iso()
        new_count = (row["run_count"] or 0) + 1
        if row["cron_expr"]:
            if row["max_runs"] is not None and new_count >= row["max_runs"]:
                conn.execute(
                    "UPDATE work_jobs SET status='done', run_count=?, last_run_at=?, last_error=NULL WHERE id=?",
                    (new_count, now, job_id),
                )
            else:
                nxt = _next_cron(row["cron_expr"])
                conn.execute(
                    """UPDATE work_jobs SET status='pending', run_count=?, last_run_at=?,
                       next_run_at=?, last_error=NULL WHERE id=?""",
                    (new_count, now, nxt, job_id),
                )
        else:
            conn.execute(
                "UPDATE work_jobs SET status='done', run_count=?, last_run_at=?, last_error=NULL WHERE id=?",
                (new_count, now, job_id),
            )
        conn.commit()
    finally:
        conn.close()


def mark_job_failed(job_id: str, error: str) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE work_jobs SET status='pending', last_error=?, last_run_at=? WHERE id=?",
            (error[:2000], _now_iso(), job_id),
        )
        conn.commit()
    finally:
        conn.close()


def pause_job(job_id: str) -> bool:
    conn = _connect()
    try:
        cur = conn.execute(
            "UPDATE work_jobs SET status='paused' WHERE id=? AND status='pending'",
            (job_id,),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def resume_job(job_id: str) -> bool:
    conn = _connect()
    try:
        cur = conn.execute(
            "UPDATE work_jobs SET status='pending' WHERE id=? AND status='paused'",
            (job_id,),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def cancel_job(job_id: str) -> bool:
    conn = _connect()
    try:
        cur = conn.execute("DELETE FROM work_jobs WHERE id=?", (job_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def queue_stats() -> dict[str, int]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT status, COUNT(*) as c FROM work_jobs GROUP BY status"
        ).fetchall()
        return {str(r["status"]): int(r["c"]) for r in rows}
    finally:
        conn.close()
