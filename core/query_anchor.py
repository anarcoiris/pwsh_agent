"""
core/query_anchor.py — Resolve the user's mission text for phase/RAG/parser context.

System nudges appended during ReAct must not replace the original user objective.
"""

from __future__ import annotations

import re
from typing import Any

_DIRECTIVE_PREFIXES = (
    "[SYSTEM]",
    "[CHAT MODE]",
    "[SYSTEM EVALUATOR",
    "[SYSTEM DIRECTIVE",
    "MISSION_COMPLETE rejected",
    "Planning phase over",
)

_DIRECTIVE_LINE_RE = re.compile(r"^\s*\[.+\]\s*$")


def strip_directives(text: str) -> str:
    """Remove bracketed directive lines from query text."""
    lines = []
    for line in (text or "").splitlines():
        if _DIRECTIVE_LINE_RE.match(line.strip()):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def is_system_directive(content: str) -> bool:
    """True if the message is primarily an automated system nudge."""
    text = (content or "").strip()
    if not text:
        return True
    head = text[:120]
    for prefix in _DIRECTIVE_PREFIXES:
        if head.startswith(prefix) or prefix in head[:80]:
            return True
    if text.startswith("[") and text.endswith("]") and "\n" not in text[:200]:
        return True
    # Chat directive wrapper before user text
    if "[CHAT MODE]" in text[:400] and len(strip_directives(text)) < 40:
        return True
    return False


def resolve_anchor_query(messages: list[dict[str, Any]], fallback: str = "") -> str:
    """First non-directive user message body, or fallback."""
    for msg in messages:
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "") or ""
        if is_system_directive(content):
            continue
        stripped = strip_directives(content)
        if stripped:
            return stripped
    fb = (fallback or "").strip()
    return strip_directives(fb) if fb else ""
