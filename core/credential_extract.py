"""
core/credential_extract.py — Parse analyze_pcapng analysis into login_forms drafts.
"""

from __future__ import annotations

import binascii
import re
from typing import Any


_LOGIN_ENTRY_RE = re.compile(r"login_entry", re.I)
_HEX_XML_RE = re.compile(r"3c616a61785f726573706f6e73655f786d6c", re.I)


def has_login_evidence(analysis: dict[str, Any]) -> bool:
    blob = "\n".join(
        str(analysis.get(k, ""))
        for k in ("http_forms", "potential_plaintext_credentials", "key_fields")
    ).lower()
    return "login_entry" in blob and ("password" in blob or "username" in blob)


def _parse_http_forms_rows(http_forms: str) -> list[str]:
    rows: list[str] = []
    if not http_forms:
        return rows
    for line in http_forms.splitlines():
        if not _LOGIN_ENTRY_RE.search(line):
            continue
        if "Password" in line or "Username" in line:
            rows.append(line.strip())
    return rows


def _decode_xml_hint_from_key_fields(key_fields: str) -> str:
    if not key_fields:
        return ""
    for line in key_fields.splitlines():
        if _HEX_XML_RE.search(line.replace(" ", "")):
            hex_part = re.search(r"\"([0-9a-f]{40,})\"", line, re.I)
            if hex_part:
                try:
                    decoded = binascii.unhexlify(hex_part.group(1)[:2000]).decode(
                        "utf-8", errors="replace"
                    )
                    if "xml" in decoded.lower() or "OBJ" in decoded:
                        return decoded[:800]
                except (binascii.Error, ValueError):
                    pass
            return (
                "XML response present as hex in key_fields (frame ~12). "
                "find_file('verbose_*.txt') then grep_file(path=<recommended>, pattern='xmlObj|ajax_response|ParaName') "
                "or grep_file('.pulse/pcap_logs/verbose_*.txt', pattern='xmlObj|ajax_response|ParaName') "
                "or decode the http.file_data hex column."
            )
    return ""


def build_login_forms_draft(analysis: dict[str, Any], max_chars: int = 4000) -> str | None:
    """Structured text the model can paste into write_file(login_forms.txt)."""
    if not analysis:
        return None
    parts: list[str] = []
    rows = _parse_http_forms_rows(str(analysis.get("http_forms", "")))
    if rows:
        parts.append("# HTTP login forms (from analyze_pcapng http_forms)")
        parts.extend(rows[:8])
    creds = str(analysis.get("potential_plaintext_credentials", "")).strip()
    if creds and creds not in parts:
        parts.append("\n# potential_plaintext_credentials")
        parts.append(creds[:1500])
    xml_hint = _decode_xml_hint_from_key_fields(str(analysis.get("key_fields", "")))
    if xml_hint:
        parts.append("\n# XML / xmlObj hint")
        parts.append(xml_hint[:1200])
    vlog = analysis.get("verbose_log_file")
    if vlog:
        parts.append(f"\n# Full decode: read_file(path=\"{vlog}\", line_start=1, line_count=120)")
    if not parts:
        return None
    text = "\n".join(parts)
    return text[:max_chars]
