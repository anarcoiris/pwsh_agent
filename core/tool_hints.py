"""Parse user messages into structured hints for crack_hash and analyze_pcapng."""

from __future__ import annotations

import re
from typing import Any

# hash_pro7 mask alphabet: N A L U ! H ?
_DEFAULT_MASK = "NNNNNNAA!"


def parse_hash_crack_hints(message: str) -> dict[str, Any]:
    """Extract hash, salt, prefix/suffix, and mask clues from natural language."""
    hints: dict[str, Any] = {}
    text = message or ""
    lower = text.lower()

    hash_m = re.search(r"\b([a-f0-9]{64})\b", text, re.I)
    if hash_m:
        hints["target_hash"] = hash_m.group(1).lower()

    # salt xmlObj "55077791"  OR  salt "55077791"  OR  salt: 55077791
    salt_complex = re.search(
        r"\bsalt\s+(?:(?P<prefix>\w+)\s+)?[\"'](?P<salt>[^\"']+)[\"']",
        text,
        re.I,
    )
    if salt_complex:
        if salt_complex.group("prefix"):
            hints["known_prefix"] = salt_complex.group("prefix")
        hints["salt"] = salt_complex.group("salt")
    else:
        for pat in (
            r"\bsalt\s*[:=]\s*[\"']([^\"']+)[\"']",
            r"\bwith\s+(?:its\s+)?salt\s+[\"']([^\"']+)[\"']",
            r"\bsalt\s+[\"']([^\"']+)[\"']",
        ):
            m = re.search(pat, text, re.I)
            if m:
                hints["salt"] = m.group(1)
                break
        if "salt" not in hints:
            m = re.search(r"\bsalt\s+([a-z0-9._-]{4,})\b", text, re.I)
            if m and m.group(1).lower() not in ("xml", "the", "is", "a"):
                hints["salt"] = m.group(1)

    if re.search(r"\bxmlobj\b|\bxml\s*obj\b", lower) and "known_prefix" not in hints:
        hints["known_prefix"] = "xmlObj"

    prefix_m = re.search(
        r"\b(?:prefix|starts?\s+with)\s+[\"']?([^\"'\s]+)[\"']?",
        text,
        re.I,
    )
    if prefix_m:
        hints["known_prefix"] = prefix_m.group(1)

    suffix_m = re.search(
        r"\b(?:suffix|ends?\s+with)\s+[\"']?([^\"'\s]+)[\"']?",
        text,
        re.I,
    )
    if suffix_m:
        hints["known_suffix"] = suffix_m.group(1)

    len_m = re.search(r"\b(\d{1,2})\s*(?:char(?:acter)?s?|digits?)\b", lower)
    if len_m:
        hints["min_len"] = int(len_m.group(1))

    if re.search(r"\b\d+\s*letter", lower) and "mask" not in hints:
        hints["mask"] = _DEFAULT_MASK
    elif re.search(r"\bmask\s*[:=]\s*([NALU!?H]+)", text, re.I):
        hints["mask"] = re.search(r"\bmask\s*[:=]\s*([NALU!?H]+)", text, re.I).group(1)
    else:
        hints.setdefault("mask", _DEFAULT_MASK)

    if re.search(r"\bwordlist\b", lower):
        wl = re.search(r"\bwordlist\s+[\"']?([^\"'\s]+)[\"']?", text, re.I)
        if wl:
            hints["wordlist"] = wl.group(1)

    return hints


def format_crack_hash_call(hints: dict[str, Any]) -> str:
    """Build an example crack_hash JSON line for goal nudges."""
    if not hints.get("target_hash"):
        return 'crack_hash target_hash="<64-char-hex>" salt="<salt-if-any>" mask="NNNNNNAA!"'

    args: list[str] = [f'target_hash="{hints["target_hash"]}"']
    if hints.get("salt"):
        args.append(f'salt="{hints["salt"]}"')
    if hints.get("known_prefix"):
        args.append(f'known_prefix="{hints["known_prefix"]}"')
    if hints.get("known_suffix"):
        args.append(f'known_suffix="{hints["known_suffix"]}"')
    args.append(f'mask="{hints.get("mask", _DEFAULT_MASK)}"')
    if hints.get("min_len") is not None:
        args.append(f'min_len={hints["min_len"]}')
    if hints.get("wordlist"):
        args.append(f'wordlist="{hints["wordlist"]}"')
    return "crack_hash " + " ".join(args)


def hash_planning_directive(hints: dict[str, Any]) -> str:
    """System directive: plan salt + mask before invoking crack_hash."""
    lines = [
        "[HASH CRACK PLAN]",
        "Before crack_hash, confirm ALL of:",
        "• target_hash — 64 hex chars (SHA-256)",
        "• salt — appended to password before hash (hashcat mode sha256(pass+salt)); REQUIRED if user mentioned salt",
        "• mask — N=digit, A=alnum, L=lower, U=upper, !=punctuation charset (NOT literal '!'), ?=any",
        "  ULLLLLLLNN!! = 1 upper + 7 lower + 2 digits + 2 punctuation picks (~750T combos at len 12)",
        "• known_prefix / known_suffix — fixed parts of the password",
        "• min_len — skip shorter candidates when password length is known",
        "Use one sequentialthinking step to state mask+salt plan, then crack_hash (not host_exec, not -t/-s).",
        "Mask charset reference: " + _DEFAULT_MASK + " means 6 digits + 2 letters + '!'.",
    ]
    if hints.get("target_hash"):
        lines.append(f"Parsed hash: {hints['target_hash'][:16]}…")
    if hints.get("salt"):
        lines.append(f"Parsed salt: {hints['salt']}")
    if hints.get("known_prefix"):
        lines.append(f"Parsed prefix: {hints['known_prefix']}")
    if hints.get("mask"):
        lines.append(f"Suggested mask: {hints['mask']}")
    if hints.get("min_len"):
        lines.append(f"Suggested min_len: {hints['min_len']}")
    return "\n".join(lines)


def parse_pcap_analysis_hints(message: str) -> dict[str, Any]:
    """Build filter and workflow hints for PCAP tasks."""
    lower = (message or "").lower()
    hints: dict[str, Any] = {}

    frame_m = re.search(r"\b(?:frame|packet)\s*#?(\d+)\b", lower)
    if frame_m:
        hints["filter_expression"] = f"frame.number == {frame_m.group(1)}"
        hints["verbose"] = True
        return hints

    filters: list[str] = []
    if "http" in lower:
        filters.append("http")
    if "login" in lower:
        filters.append('http contains "login" or http.request.uri contains "login"')
    if "password" in lower:
        filters.append('http contains "password" or ftp or smtp')
    if "xml" in lower or "xmlobj" in lower:
        filters.append('http contains "xml" or http.content_type contains "xml"')
    if "credential" in lower or "auth" in lower:
        filters.append("http.authorization or ftp or smtp")

    if filters:
        hints["filter_expression"] = " or ".join(f"({f})" for f in filters)
    else:
        hints["filter_expression"] = "http"

    hints["verbose"] = bool(
        re.search(r"\b(decode|verbose|contents|key\s*values?|extract|field)\b", lower)
    )
    hints["workflow"] = (
        "1) find_file(name) 2) analyze_pcapng(http, limit=30) index 3) narrow filter 4) verbose=true for fields"
    )
    return hints


def pcap_planning_directive(hints: dict[str, Any], path_hint: str = "last_capture.pcapng") -> str:
    filt = hints.get("filter_expression", "http")
    verb = "true" if hints.get("verbose") else "false"
    return (
        "[PCAP ANALYSIS PLAN]\n"
        f"File: use find_file first; typical path `{path_hint}`.\n"
        "Workflow:\n"
        "1. find_file → use recommended path\n"
        f"2. analyze_pcapng(file_path=..., filter_expression=\"{filt}\", limit=30, verbose=false) — index packets\n"
        "3. Refine filter (http contains \"login\", frame.number == N, etc.)\n"
        f"4. analyze_pcapng(..., filter_expression=\"<narrow>\", verbose={verb}, limit=10) — decode fields\n"
        "5. If verbose_log_file returned, read_file that path in chunks with line_start/line_count; continue with next_line_start\n"
        "Do NOT use encode_decode on PCAP bytes. Display filters only (not capture -f).\n"
        f"{hints.get('workflow', '')}"
    )
