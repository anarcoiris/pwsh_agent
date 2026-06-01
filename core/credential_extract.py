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
                "find_file('verbose_*.txt') then find_and_grep(pattern='xml|Password|Username|616a6178|xmlObj', "
                "path_glob='.pulse/pcap_logs/verbose_*.txt', case_insensitive=true) "
                "or decode the http.file_data hex column."
            )
    return ""


_LOGIN_HASH_RE = re.compile(
    r'"([0-9a-f]{64}),([^,\s"]+),(\d+),login"',
    re.I,
)
_AJAX_SALT_RE = re.compile(
    r"<ajax_response_xml_root>(\d+)</ajax_response_xml_root>",
    re.I,
)


def parse_login_hashes(http_forms: str) -> list[dict[str, str]]:
    """Extract SHA-256 password hashes from analyze_pcapng http_forms rows."""
    entries: list[dict[str, str]] = []
    for m in _LOGIN_HASH_RE.finditer(http_forms or ""):
        entries.append({
            "hash": m.group(1).lower(),
            "username": m.group(2),
            "session_token": m.group(3),
        })
    return entries


def find_xml_salts(text: str) -> list[str]:
    """Find xmlObj salt values (ajax_response_xml_root) in plain or hex-encoded text."""
    salts: list[str] = []
    seen: set[str] = set()

    def _add(val: str) -> None:
        if val and val not in seen:
            seen.add(val)
            salts.append(val)

    for m in _AJAX_SALT_RE.finditer(text or ""):
        _add(m.group(1))

    hex_patterns = (
        r'"([0-9a-f]{24,})"',
        r"\b([0-9a-f]{80,})\b",
    )
    for pattern in hex_patterns:
        for hex_m in re.finditer(pattern, text or "", re.I):
            try:
                decoded = binascii.unhexlify(hex_m.group(1)[:8000]).decode(
                    "utf-8", errors="replace"
                )
            except (binascii.Error, ValueError):
                continue
            for m in _AJAX_SALT_RE.finditer(decoded):
                _add(m.group(1))
    return salts


def pair_hashes_with_salts(
    hashes: list[dict[str, str]],
    salts: list[str],
) -> list[dict[str, str]]:
    """Pair login_entry hashes with login_token xmlObj salts (ordered)."""
    pairs: list[dict[str, str]] = []
    for i, entry in enumerate(hashes):
        salt = salts[i] if i < len(salts) else (salts[-1] if salts else "")
        pairs.append({**entry, "salt": salt})
    return pairs


def extract_hash_salt_pairs(analysis: dict[str, Any]) -> list[dict[str, str]]:
    """Build hash+salt pairs from a single analyze_pcapng analysis dict."""
    if not analysis:
        return []
    hashes = parse_login_hashes(str(analysis.get("http_forms", "")))
    if not hashes:
        return []
    blob = "\n".join(
        str(analysis.get(k, ""))
        for k in ("key_fields", "packet_summary", "potential_plaintext_credentials", "http_index")
    )
    salts = find_xml_salts(blob)
    return pair_hashes_with_salts(hashes, salts)


def build_cracked_deliverable(
    pairs: list[dict[str, str]],
    crack_results: list[dict[str, Any]],
) -> str:
    """Format pwd*.txt content with hash, salt, and cracked plaintext."""
    lines = ["# Password hash / salt / cracked results", ""]
    for i, pair in enumerate(pairs):
        lines.append(f"## entry_{i + 1}")
        lines.append(f"hash={pair.get('hash', '')}")
        lines.append(f"salt={pair.get('salt', '')}")
        lines.append(f"username={pair.get('username', '')}")
        if i < len(crack_results):
            res = crack_results[i]
            plain = res.get("password") or res.get("plaintext")
            if plain:
                lines.append(f"plaintext={plain}")
            else:
                status = res.get("status") or res.get("error") or "unknown"
                lines.append(f"plaintext=NOT CRACKED ({status})")
                mask = res.get("mask")
                if mask:
                    lines.append(f"mask_tried={mask}")
        else:
            lines.append("plaintext=NOT ATTEMPTED")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def build_login_forms_draft(analysis: dict[str, Any], max_chars: int = 8000) -> str | None:
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