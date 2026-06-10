"""core/session_db.py — SQLite session state persistence.

Inspired by NanoClaw v2's two-DB split, adapted for a single-process Python agent.
Consolidates scattered JSON files (agent_autonomous.json, plan_state.json, facts.json,
intent_spec.json, working_memory.json, handoff.json) into a single session.db file.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.session_paths import session_state_dir, list_session_ids

logger = logging.getLogger("pwsh_agent.core.session_db")

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS messages (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    role        TEXT NOT NULL,          -- system | user | assistant | tool
    content     TEXT NOT NULL,
    name        TEXT,                   -- tool name (when role=tool)
    tool_calls  TEXT,                   -- JSON array (when role=assistant with calls)
    timestamp   TEXT NOT NULL,          -- ISO-8601 UTC
    trimmed     INTEGER DEFAULT 0       -- 1 = content was compacted by trim_context
);

CREATE TABLE IF NOT EXISTS session_state (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,          -- JSON blob
    updated_at  TEXT NOT NULL           -- ISO-8601 UTC
);

CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY
);

INSERT OR IGNORE INTO schema_version (version) VALUES (1);
"""


class SessionDB:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.path = session_state_dir(session_id) / "session.db"
        self._conn = self._connect()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(_SCHEMA_SQL)
        conn.commit()
        return conn

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass

    def add_message(self, msg: dict[str, Any]) -> int:
        """Insert a message into the messages table. Returns the new sequence number."""
        role = str(msg.get("role", "user"))
        content = str(msg.get("content", ""))
        name = msg.get("name")
        tool_calls = msg.get("tool_calls")
        trimmed = 1 if msg.get("trimmed") else 0
        timestamp = msg.get("timestamp") or datetime.now(timezone.utc).isoformat()

        tool_calls_json = None
        if tool_calls is not None:
            tool_calls_json = json.dumps(tool_calls, default=str)

        cursor = self._conn.execute(
            """INSERT INTO messages (role, content, name, tool_calls, timestamp, trimmed)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (role, content, name, tool_calls_json, timestamp, trimmed),
        )
        self._conn.commit()
        return cursor.lastrowid

    def get_messages(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Retrieve all messages, or up to `limit` recent messages, ordered by sequence."""
        if limit is not None:
            # Subquery to get the last N messages, then sort them ascending
            query = """
                SELECT * FROM (
                    SELECT * FROM messages ORDER BY seq DESC LIMIT ?
                ) ORDER BY seq ASC
            """
            rows = self._conn.execute(query, (limit,)).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM messages ORDER BY seq ASC").fetchall()

        return [self._row_to_msg(r) for r in rows]

    def get_recent(self, n: int) -> list[dict[str, Any]]:
        """Retrieve the last `n` messages."""
        return self.get_messages(limit=n)

    def update_message(self, seq: int, content: str) -> None:
        """Update the content of a message by sequence."""
        self._conn.execute(
            "UPDATE messages SET content = ? WHERE seq = ?",
            (content, seq),
        )
        self._conn.commit()

    def mark_trimmed(self, seq: int) -> None:
        """Mark a message as trimmed/compacted."""
        self._conn.execute(
            "UPDATE messages SET trimmed = 1 WHERE seq = ?",
            (seq,),
        )
        self._conn.commit()

    def count(self) -> int:
        """Count the number of messages."""
        row = self._conn.execute("SELECT COUNT(*) FROM messages").fetchone()
        return row[0] if row else 0

    def _row_to_msg(self, row: sqlite3.Row) -> dict[str, Any]:
        msg: dict[str, Any] = {
            "role": row["role"],
            "content": row["content"],
        }
        # Include seq for utility if needed (e.g. updating messages)
        msg["seq"] = row["seq"]
        if row["name"] is not None:
            msg["name"] = row["name"]
        if row["tool_calls"] is not None:
            try:
                msg["tool_calls"] = json.loads(row["tool_calls"])
            except json.JSONDecodeError:
                msg["tool_calls"] = []
        if row["timestamp"]:
            msg["timestamp"] = row["timestamp"]
        if row["trimmed"]:
            msg["trimmed"] = True
        return msg

    # Key-value state management (replaces JSON state files)
    def get_state(self, key: str) -> Any | None:
        """Retrieve and deserialize a state value by key."""
        row = self._conn.execute(
            "SELECT value FROM session_state WHERE key = ?",
            (key,),
        ).fetchone()
        if row:
            try:
                return json.loads(row["value"])
            except json.JSONDecodeError:
                return None
        return None

    def set_state(self, key: str, value: Any) -> None:
        """Serialize and persist a state value by key."""
        val_json = json.dumps(value, default=str)
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """INSERT INTO session_state (key, value, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
            (key, val_json, now),
        )
        self._conn.commit()

    def delete_state(self, key: str) -> None:
        """Delete a state key."""
        self._conn.execute("DELETE FROM session_state WHERE key = ?", (key,))
        self._conn.commit()

    @staticmethod
    def search_messages(pattern: str, max_sessions: int = 5) -> list[dict[str, Any]]:
        """Search for a text pattern in messages across multiple session databases.

        Returns a list of dicts: [{'session_id': ..., 'seq': ..., 'role': ..., 'content': ..., 'timestamp': ...}]
        """
        results = []
        session_ids = list_session_ids()
        count = 0
        for sid in session_ids:
            db_path = session_state_dir(sid) / "session.db"
            if not db_path.is_file():
                continue

            try:
                # Open read-only/timeout connection to avoid locking
                conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5.0)
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT seq, role, content, timestamp FROM messages WHERE content LIKE ? ORDER BY seq ASC",
                    (f"%{pattern}%",),
                ).fetchall()
                for r in rows:
                    results.append({
                        "session_id": sid,
                        "seq": r["seq"],
                        "role": r["role"],
                        "content": r["content"],
                        "timestamp": r["timestamp"],
                    })
                conn.close()
                count += 1
                if count >= max_sessions:
                    break
            except Exception as e:
                logger.warning("Error searching messages in session %s: %s", sid, e)

        return results
