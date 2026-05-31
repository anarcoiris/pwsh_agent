"""Session-scoped structured facts persistence."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.session_paths import facts_file


def _default() -> dict[str, Any]:
    return {
        "updated_at": "",
        "pcap": {
            "path": "",
            "verbose_log_file": "",
            "keywords": [],
            "http_forms_preview": "",
            "credentials_preview": "",
        },
        "credentials": [],
        "hosts": {"live": [], "open_ports": []},
        "intel": {"cves": []},
    }


def load_facts(session_id: str) -> dict[str, Any]:
    path = facts_file(session_id)
    if not path.is_file():
        return _default()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            base = _default()
            base.update(data)
            return base
    except (OSError, json.JSONDecodeError):
        pass
    return _default()


def save_facts(session_id: str, facts: dict[str, Any]) -> Path:
    path = facts_file(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    facts = dict(facts)
    facts["updated_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(facts, indent=2, default=str), encoding="utf-8")
    return path


def update_from_tool(session_id: str, tool_name: str, result: dict[str, Any], args: dict[str, Any] | None = None) -> Path | None:
    if not isinstance(result, dict) or result.get("success") is False:
        return None

    facts = load_facts(session_id)
    args = args or {}

    if tool_name == "analyze_pcapng":
        analysis = result.get("analysis") if isinstance(result.get("analysis"), dict) else {}
        facts["pcap"]["path"] = str(args.get("file_path") or facts["pcap"].get("path") or "")
        if analysis.get("verbose_log_file"):
            facts["pcap"]["verbose_log_file"] = str(analysis.get("verbose_log_file"))
        blob = "\n".join(
            str(analysis.get(k, ""))
            for k in ("key_fields", "potential_plaintext_credentials", "http_forms", "packet_summary")
        )
        kws = set(facts["pcap"].get("keywords", []))
        for kw in ("login", "password", "xmlobj", "username", "sessiontoken"):
            if kw in blob.lower():
                kws.add(kw)
        facts["pcap"]["keywords"] = sorted(kws)
        http_forms = str(analysis.get("http_forms", "")).strip()
        if http_forms:
            facts["pcap"]["http_forms_preview"] = http_forms[:500]
        cred_preview = str(analysis.get("potential_plaintext_credentials", "")).strip()
        if cred_preview:
            facts["pcap"]["credentials_preview"] = cred_preview[:500]
        key_fields = str(analysis.get("key_fields", "")).strip()
        if key_fields and not cred_preview:
            facts["pcap"]["credentials_preview"] = key_fields[:500]
        hashes = set(re.findall(r"\b[a-fA-F0-9]{64}\b", blob))
        if hashes:
            creds = facts.get("credentials", [])
            for h in sorted(hashes)[:5]:
                entry = {"source": "analyze_pcapng", "sha256": h}
                if entry not in creds:
                    creds.append(entry)
            facts["credentials"] = creds
        return save_facts(session_id, facts)

    if tool_name == "ping_sweep":
        live = result.get("live_hosts", []) if isinstance(result.get("live_hosts"), list) else []
        ips = []
        for item in live:
            if isinstance(item, dict):
                ip = str(item.get("IP", "")).strip()
                if ip:
                    ips.append(ip)
            elif isinstance(item, str):
                ips.append(item.strip())
        if ips:
            merged = sorted(set(facts["hosts"].get("live", []) + [ip for ip in ips if ip]))
            facts["hosts"]["live"] = merged
            return save_facts(session_id, facts)
        return None

    if tool_name == "port_scan":
        open_ports = result.get("open_ports", []) if isinstance(result.get("open_ports"), list) else []
        serial: list[str] = []
        for p in open_ports:
            serial.append(str(p))
        if serial:
            merged = sorted(set(facts["hosts"].get("open_ports", []) + serial))
            facts["hosts"]["open_ports"] = merged
            return save_facts(session_id, facts)
        return None

    if tool_name == "cve_lookup":
        items = result.get("cves", []) if isinstance(result.get("cves"), list) else []
        cves = set(facts["intel"].get("cves", []))
        for it in items:
            if isinstance(it, dict):
                cid = str(it.get("id", "")).strip()
                if cid:
                    cves.add(cid)
        if cves:
            facts["intel"]["cves"] = sorted(cves)
            return save_facts(session_id, facts)
        return None

    return None


def summarize_facts(session_id: str, max_chars: int = 700) -> str:
    data = load_facts(session_id)
    lines: list[str] = []
    pcap = data.get("pcap", {})
    if pcap.get("path"):
        lines.append(f"pcap.path={pcap.get('path')}")
    if pcap.get("verbose_log_file"):
        lines.append(f"pcap.verbose_log_file={pcap.get('verbose_log_file')}")
    kws = pcap.get("keywords") or []
    if kws:
        lines.append(f"pcap.keywords={','.join(kws[:8])}")
    preview = pcap.get("http_forms_preview") or pcap.get("credentials_preview")
    if preview:
        one_line = preview.replace("\n", " ")[:200]
        lines.append(f"pcap.credential_preview={one_line}")
    creds = data.get("credentials") or []
    if creds:
        lines.append(f"credentials.count={len(creds)}")
    live = data.get("hosts", {}).get("live") or []
    if live:
        lines.append(f"hosts.live={','.join(live[:8])}")
    ports = data.get("hosts", {}).get("open_ports") or []
    if ports:
        lines.append(f"hosts.open_ports={','.join(ports[:12])}")
    cves = data.get("intel", {}).get("cves") or []
    if cves:
        lines.append(f"intel.cves={','.join(cves[:8])}")
    if not lines:
        return ""
    out = "[SESSION FACTS]\n" + "\n".join(lines)
    return out[:max_chars]
