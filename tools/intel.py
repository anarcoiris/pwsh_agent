"""
tools/intel.py - Intelligence utilities: encode/decode, hash identification, findings management.
All run in-process — no external network calls, no Docker required.
"""
import base64
import hashlib
import json
import re
import sqlite3
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

from core.runtime_paths import app_root

_DB_PATH = app_root() / "state" / "findings.db"


# ──────────────────────────────────────────────
# Encode / Decode
# ──────────────────────────────────────────────

def encode_decode(text: str, operation: str, encoding: str = "base64") -> dict:
    """
    Encode or decode text using common schemes.

    Args:
        text: Input text to process.
        operation: 'encode' or 'decode'.
        encoding: Scheme — base64 | base64url | hex | url | rot13 | utf8_bytes (default: base64).

    Returns:
        Dict with result string.
    """
    try:
        op = operation.lower()
        enc = encoding.lower()

        if enc == "base64":
            if op == "encode":
                return {"success": True, "result": base64.b64encode(text.encode()).decode()}
            else:
                return {"success": True, "result": base64.b64decode(text).decode("utf-8", errors="replace")}

        elif enc == "base64url":
            if op == "encode":
                return {"success": True, "result": base64.urlsafe_b64encode(text.encode()).decode()}
            else:
                return {"success": True, "result": base64.urlsafe_b64decode(text + "==").decode("utf-8", errors="replace")}

        elif enc == "hex":
            if op == "encode":
                return {"success": True, "result": text.encode().hex()}
            else:
                return {"success": True, "result": bytes.fromhex(text).decode("utf-8", errors="replace")}

        elif enc == "url":
            if op == "encode":
                return {"success": True, "result": urllib.parse.quote(text, safe="")}
            else:
                return {"success": True, "result": urllib.parse.unquote(text)}

        elif enc == "rot13":
            import codecs
            return {"success": True, "result": codecs.encode(text, "rot_13")}

        elif enc == "utf8_bytes":
            if op == "encode":
                return {"success": True, "result": str(list(text.encode("utf-8")))}
            else:
                # Expect comma-separated ints
                nums = [int(x.strip().strip("[]")) for x in text.replace("[", "").replace("]", "").split(",")]
                return {"success": True, "result": bytes(nums).decode("utf-8", errors="replace")}

        else:
            return {"success": False, "error": f"Unknown encoding: {encoding}. Use base64, base64url, hex, url, rot13, utf8_bytes."}

    except Exception as e:
        return {"success": False, "error": str(e)}


# ──────────────────────────────────────────────
# Hash Identification
# ──────────────────────────────────────────────

_HASH_PATTERNS = [
    (r"^[a-f0-9]{32}$",  "MD5"),
    (r"^[a-f0-9]{40}$",  "SHA-1"),
    (r"^[a-f0-9]{56}$",  "SHA-224"),
    (r"^[a-f0-9]{64}$",  "SHA-256"),
    (r"^[a-f0-9]{96}$",  "SHA-384"),
    (r"^[a-f0-9]{128}$", "SHA-512"),
    (r"^\$2[aby]\$\d{2}\$.{53}$", "bcrypt"),
    (r"^\$1\$.{1,8}\$.{22}$",     "MD5-crypt"),
    (r"^\$5\$.{1,16}\$.{43}$",    "SHA-256-crypt"),
    (r"^\$6\$.{1,16}\$.{86}$",    "SHA-512-crypt"),
    (r"^\$argon2(id|i|d)\$",      "Argon2"),
    (r"^pbkdf2_sha256\$.+$",      "PBKDF2-SHA256 (Django)"),
    (r"^[a-f0-9]{32}:.+$",        "MD5 + salt"),
    (r"^[a-f0-9]{64}:.+$",        "SHA-256 + salt"),
    (r"^\{SHA\}",                  "SHA-1 (LDAP)"),
    (r"^\{SSHA\}",                 "SHA-1 + salt (LDAP)"),
    (r"^[A-Za-z0-9+/]{24}={0,2}$","Base64 (possible hash)"),
]

def hash_identify(hash_value: str) -> dict:
    """
    Identify the likely hash algorithm of a given hash string by pattern matching.

    Args:
        hash_value: The hash string to analyze.

    Returns:
        Dict with list of possible hash types and confidence notes.
    """
    h = hash_value.strip()
    matches = []
    for pattern, name in _HASH_PATTERNS:
        if re.match(pattern, h, re.IGNORECASE):
            matches.append(name)

    if matches:
        return {"success": True, "hash": h, "possible_types": matches, "length": len(h)}
    return {
        "success": True,
        "hash": h,
        "possible_types": ["Unknown"],
        "length": len(h),
        "note": "No known pattern matched. Could be a custom or obfuscated hash."
    }


# ──────────────────────────────────────────────
# Findings Database (SQLite)
# ──────────────────────────────────────────────

def _get_db():
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS findings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            severity TEXT NOT NULL,
            target TEXT,
            description TEXT,
            evidence TEXT,
            recommendation TEXT,
            created_at TEXT NOT NULL,
            specialist TEXT DEFAULT 'lead',
            session_id TEXT DEFAULT ''
        )
    """)
    try:
        conn.execute("ALTER TABLE findings ADD COLUMN session_id TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    return conn


def finding_create(
    title: str,
    severity: str,
    description: str,
    target: str = "",
    evidence: str = "",
    recommendation: str = "",
    specialist: str = "lead",
    session_id: str = "",
) -> dict:
    """
    Create and persist a new security finding to the local SQLite database.

    Args:
        title: Short descriptive title of the finding.
        severity: Severity level — CRITICAL | HIGH | MEDIUM | LOW | INFO.
        description: Detailed description of what was found.
        target: Affected host, URL, or file path (optional).
        evidence: Raw evidence snippet (output, log, etc.) (optional).
        recommendation: Suggested remediation steps (optional).
        specialist: Active specialist mode at time of finding (default: lead).

    Returns:
        Dict with the new finding ID and confirmation.
    """
    valid_severities = {"CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"}
    sev = severity.upper()
    if sev not in valid_severities:
        return {"success": False, "error": f"Invalid severity '{severity}'. Use: {', '.join(valid_severities)}"}

    created_at = datetime.now(timezone.utc).isoformat()
    # region agent log
    try:
        from core.debug_log import trace
        trace("tools.intel.finding_create", "finding created", {
            "title": title, "severity": sev, "target": target,
            "description": (description or "")[:400], "evidence": (evidence or "")[:300],
        })
    except Exception:
        pass
    # endregion
    try:
        conn = _get_db()
        sid = (session_id or "").strip()
        cur = conn.execute(
            "INSERT INTO findings (title, severity, target, description, evidence, recommendation, created_at, specialist, session_id) VALUES (?,?,?,?,?,?,?,?,?)",
            (title, sev, target, description, evidence, recommendation, created_at, specialist, sid)
        )
        conn.commit()
        finding_id = cur.lastrowid
        conn.close()
        return {"success": True, "finding_id": finding_id, "title": title, "severity": sev, "created_at": created_at}
    except Exception as e:
        return {"success": False, "error": str(e)}


def finding_list(severity_filter: str = "", limit: int = 50) -> dict:
    """
    List persisted findings from the local database, optionally filtered by severity.

    Args:
        severity_filter: Optional severity to filter by — CRITICAL | HIGH | MEDIUM | LOW | INFO.
        limit: Maximum number of findings to return (default: 50).

    Returns:
        Dict with list of findings.
    """
    try:
        conn = _get_db()
        if severity_filter:
            rows = conn.execute(
                "SELECT id, title, severity, target, description, created_at, specialist FROM findings WHERE severity=? ORDER BY id DESC LIMIT ?",
                (severity_filter.upper(), limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, title, severity, target, description, created_at, specialist FROM findings ORDER BY id DESC LIMIT ?",
                (limit,)
            ).fetchall()
        conn.close()

        findings = [
            {"id": r[0], "title": r[1], "severity": r[2], "target": r[3],
             "description": r[4][:200], "created_at": r[5], "specialist": r[6]}
            for r in rows
        ]
        return {"success": True, "count": len(findings), "findings": findings}
    except Exception as e:
        return {"success": False, "error": str(e)}


def report_generate(
    output_format: str = "markdown",
    title: str = "Pulse Agent Engagement Report",
    scope: str = "session",
    session_id: str = "",
    task_summary: str = "",
) -> dict:
    """
    Generate a structured engagement report from persisted findings.

    Args:
        output_format: Output format — markdown | text (default: markdown).
        title: Report title.
        scope: ``session`` (default) — only findings from the current session;
               ``all`` — entire findings database (legacy engagement reports).
        session_id: Session id for ``scope=session`` (``YYYYMMDD_HHMMSS``).
        task_summary: When no session findings exist, optional free-text summary
                      for an ad-hoc task report (e.g. HTTP fetch analysis).

    Returns:
        Dict with the report file path and summary.
    """
    try:
        from core.session_paths import load_active_session_id, session_start_iso

        sid = (session_id or "").strip() or load_active_session_id()
        scope_norm = (scope or "session").strip().lower()

        conn = _get_db()
        if scope_norm == "all":
            rows = conn.execute(
                "SELECT id, title, severity, target, description, evidence, recommendation, created_at, specialist, session_id "
                "FROM findings ORDER BY CASE severity WHEN 'CRITICAL' THEN 1 WHEN 'HIGH' THEN 2 WHEN 'MEDIUM' THEN 3 WHEN 'LOW' THEN 4 ELSE 5 END, id"
            ).fetchall()
        else:
            start_iso = session_start_iso(sid)
            if start_iso:
                rows = conn.execute(
                    "SELECT id, title, severity, target, description, evidence, recommendation, created_at, specialist, session_id "
                    "FROM findings WHERE (session_id = ? OR (session_id = '' AND created_at >= ?)) "
                    "ORDER BY CASE severity WHEN 'CRITICAL' THEN 1 WHEN 'HIGH' THEN 2 WHEN 'MEDIUM' THEN 3 WHEN 'LOW' THEN 4 ELSE 5 END, id",
                    (sid, start_iso),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, title, severity, target, description, evidence, recommendation, created_at, specialist, session_id "
                    "FROM findings WHERE session_id = ? "
                    "ORDER BY CASE severity WHEN 'CRITICAL' THEN 1 WHEN 'HIGH' THEN 2 WHEN 'MEDIUM' THEN 3 WHEN 'LOW' THEN 4 ELSE 5 END, id",
                    (sid,),
                ).fetchall()
        conn.close()

        # region agent log
        try:
            from core.debug_log import trace
            trace("tools.intel.report_generate", "findings pulled from DB", {
                "requested_title": title,
                "scope": scope_norm,
                "session_id": sid,
                "rows": len(rows),
                "findings": [
                    {"title": r[1], "severity": r[2], "target": r[3], "created_at": r[7], "specialist": r[8]}
                    for r in rows
                ],
            })
        except Exception:
            pass
        # endregion

        if not rows:
            summary = (task_summary or "").strip()
            if not summary:
                return {
                    "success": False,
                    "error": (
                        f"No findings recorded for session '{sid}' (scope=session). "
                        "Do NOT call report_generate for fetch/analyze tasks unless you first "
                        "persist findings with finding_create, or pass task_summary with your analysis. "
                        "Otherwise summarize results in your reply to the user."
                    ),
                    "scope": scope_norm,
                    "session_id": sid,
                }
            # Ad-hoc task report (no DB findings)
            now = datetime.now(timezone.utc)
            date_str = now.strftime("%Y-%m-%d %H:%M UTC")
            report_dir = app_root() / "output"
            report_dir.mkdir(parents=True, exist_ok=True)
            fname = f"report_{now.strftime('%Y%m%d_%H%M%S')}.md"
            report_path = report_dir / fname
            report_text = (
                f"# {title}\n\n"
                f"**Generated:** {date_str}  \n"
                f"**Session:** `{sid}`  \n"
                f"**Scope:** task summary (no formal findings in DB for this session)\n\n"
                f"---\n\n## Task Summary\n\n{summary}\n"
            )
            with open(report_path, "w", encoding="utf-8") as f:
                f.write(report_text)
            return {
                "success": True,
                "report_path": str(report_path),
                "findings_count": 0,
                "severity_summary": {},
                "title": title,
                "scope": "task_summary",
            }

        now = datetime.now(timezone.utc)
        date_str = now.strftime("%Y-%m-%d %H:%M UTC")

        report_dir = app_root() / "output"
        report_dir.mkdir(parents=True, exist_ok=True)
        fname = f"report_{now.strftime('%Y%m%d_%H%M%S')}.md"
        report_path = report_dir / fname

        severity_counts = {}
        lines = [
            f"# {title}",
            f"\n**Generated:** {date_str}  ",
            f"**Total Findings:** {len(rows)}\n",
            "---\n",
            "## Executive Summary\n",
        ]

        for row in rows:
            sev = row[2]
            severity_counts[sev] = severity_counts.get(sev, 0) + 1

        for sev in ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]:
            count = severity_counts.get(sev, 0)
            if count:
                lines.append(f"- **{sev}**: {count}")

        lines.append("\n---\n\n## Findings\n")

        for _id, t, sev, target, desc, evid, rec, ts, spec, _sid in rows:
            sev_emoji = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🔵", "INFO": "⚪"}.get(sev, "⚪")
            lines.append(f"### [{sev_emoji} {sev}] {t}")
            if target:
                lines.append(f"**Target:** `{target}`  ")
            lines.append(f"**Specialist:** {spec}  **Date:** {ts[:10]}\n")
            lines.append(f"{desc}\n")
            if evid:
                lines.append(f"**Evidence:**\n```\n{evid}\n```\n")
            if rec:
                lines.append(f"**Recommendation:** {rec}\n")
            lines.append("---\n")

        report_text = "\n".join(lines)
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report_text)

        return {
            "success": True,
            "report_path": str(report_path),
            "findings_count": len(rows),
            "severity_summary": severity_counts,
            "title": title,
            "scope": scope_norm,
            "session_id": sid,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
