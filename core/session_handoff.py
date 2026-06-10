"""Seal session outcomes into compact handoff packets for LEAD cross-session review."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.facts_store import load_facts, summarize_facts
from core.session_paths import session_state_dir


def handoff_file(session_id: str) -> Path:
    return session_state_dir(session_id) / "handoff.json"


def _clip(text: str, limit: int = 400) -> str:
    return (text or "").replace("\n", " ").strip()[:limit]


def _report_summary(report_path: Path, max_chars: int = 500) -> str:
    if not report_path.is_file():
        return ""
    try:
        text = report_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    lines: list[str] = []
    in_exec = False
    for line in text.splitlines():
        if line.startswith("## Executive Summary"):
            in_exec = True
            continue
        if in_exec and line.startswith("##"):
            break
        if in_exec and line.strip():
            lines.append(line.strip())
    if lines:
        return _clip(" ".join(lines), max_chars)
    return _clip(text, max_chars)


def _load_intent_domain(session_id: str) -> str:
    path = session_state_dir(session_id) / "intent_spec.json"
    if not path.is_file():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return str(data.get("domain", "") or "")
    except (OSError, json.JSONDecodeError):
        return ""


def seal_handoff(
    session_id: str,
    *,
    outcome: str = "partial",
    continues_from: str | None = None,
) -> Path | None:
    """Build and persist handoff.json from facts, findings, and optional report."""
    sid = (session_id or "").strip()
    if not sid:
        return None

    facts = load_facts(sid)
    facts_digest = summarize_facts(sid, max_chars=600)
    if facts_digest.startswith("[SESSION FACTS]"):
        facts_digest = facts_digest[len("[SESSION FACTS]"):].strip()

    findings_head: list[dict[str, str]] = []
    report_path = ""
    try:
        import tools

        fl = tools.finding_list(session_id=sid, scope="session")
        if isinstance(fl, dict) and fl.get("success"):
            for f in (fl.get("findings") or [])[:8]:
                if isinstance(f, dict):
                    findings_head.append({
                        "title": _clip(str(f.get("title", "")), 80),
                        "severity": str(f.get("severity", "")),
                        "target": _clip(str(f.get("target", "")), 80),
                    })
    except Exception:
        pass

    for art in facts.get("artifacts") or []:
        if not isinstance(art, dict):
            continue
        p = str(art.get("path", ""))
        if "report_" in p and p.endswith(".md"):
            report_path = p
            break

    report_summary = ""
    if report_path:
        rp = Path(report_path)
        if not rp.is_file():
            from core.runtime_paths import app_root

            rp = app_root() / report_path.replace("\\", "/").lstrip("/")
        report_summary = _report_summary(rp)

    handoff_idx = facts.get("handoff") if isinstance(facts.get("handoff"), dict) else {}
    domain = _load_intent_domain(sid) or str(handoff_idx.get("domain", "") or "")

    summary_parts: list[str] = []
    if domain:
        summary_parts.append(f"domain={domain}")
    if findings_head:
        summary_parts.append(f"findings={len(findings_head)}")
    if report_path:
        summary_parts.append(f"report={report_path}")
    summary = "; ".join(summary_parts) or f"Session {sid} sealed."

    artifact_pointers: list[str] = []
    for art in facts.get("artifacts") or []:
        if isinstance(art, dict) and art.get("path"):
            artifact_pointers.append(str(art["path"])[:120])
    pcap = facts.get("pcap") if isinstance(facts.get("pcap"), dict) else {}
    if pcap.get("path"):
        artifact_pointers.append(str(pcap["path"])[:120])

    payload: dict[str, Any] = {
        "session_id": sid,
        "sealed_at": datetime.now(timezone.utc).isoformat(),
        "domain": domain,
        "outcome": outcome,
        "summary": summary,
        "findings": findings_head,
        "facts_digest": facts_digest,
        "report_path": report_path,
        "report_summary": report_summary,
        "artifact_pointers": artifact_pointers[:8],
        "continues_from": continues_from,
    }

    from core.session_paths import session_state_dir
    db_path = session_state_dir(sid) / "session.db"
    if db_path.is_file():
        from core.session_db import SessionDB
        db = SessionDB(sid)
        try:
            db.set_state("handoff", payload)
        finally:
            db.close()
        return handoff_file(sid)

    path = handoff_file(sid)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path
    except OSError:
        return None


def load_handoff(session_id: str) -> dict[str, Any] | None:
    from core.session_paths import session_state_dir
    db_path = session_state_dir(session_id) / "session.db"
    
    data = None
    if db_path.is_file():
        from core.session_db import SessionDB
        db = SessionDB(session_id)
        try:
            data = db.get_state("handoff")
        finally:
            db.close()
    else:
        path = handoff_file(session_id)
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
    return data if isinstance(data, dict) else None


def format_handoff_for_state(handoff: dict[str, Any] | None, max_chars: int = 600) -> str:
    if not handoff:
        return ""
    lines = [
        f"session={handoff.get('session_id', '')}",
        f"domain={handoff.get('domain', '')}",
        f"outcome={handoff.get('outcome', '')}",
        f"summary={handoff.get('summary', '')}",
    ]
    if handoff.get("report_path"):
        lines.append(f"report={handoff['report_path']}")
    if handoff.get("report_summary"):
        lines.append(f"report_summary={handoff['report_summary']}")
    findings = handoff.get("findings") or []
    if findings:
        fh = "; ".join(
            f"{f.get('severity', '?')}:{f.get('title', '')[:40]}"
            for f in findings[:6]
            if isinstance(f, dict)
        )
        lines.append(f"findings={fh}")
    if handoff.get("facts_digest"):
        lines.append(str(handoff["facts_digest"])[:200])
    arts = handoff.get("artifact_pointers") or []
    if arts:
        lines.append("artifacts=" + ", ".join(str(a) for a in arts[:4]))
    return "\n".join(lines)[:max_chars]


def list_sealed_handoffs(limit: int = 8) -> list[dict[str, Any]]:
    from core.session_paths import list_session_ids

    out: list[dict[str, Any]] = []
    for sid in list_session_ids():
        h = load_handoff(sid)
        if h:
            out.append(h)
        if len(out) >= limit:
            break
    return out
