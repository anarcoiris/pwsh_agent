"""
core/tool_index.py — Static tool-routing index for always-on ContextRouter injection.

Loads knowledge/tools/tool_routing_static.md once (cache invalidated by mtime).
"""

from __future__ import annotations

import re
from pathlib import Path

from core.runtime_paths import app_root

_STATIC_PATH = app_root() / "knowledge" / "tools" / "tool_routing_static.md"
_FRONTMATTER_RE = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)
_SECTION_RE = re.compile(r"(?=(?:^|\n)#+\s+)", re.MULTILINE)

_cache_mtime: float | None = None
_cache_body: str = ""

DEFAULT_STATIC_MAX_CHARS = 1800


def _strip_frontmatter(raw: str) -> str:
    return _FRONTMATTER_RE.sub("", raw, count=1).strip()


def _extract_section(body: str, title: str) -> str:
    """Return markdown section body for a heading whose title contains *title*."""
    parts = _SECTION_RE.split(body)
    needle = title.lower()
    for part in parts:
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^#+\s+(.+)", part)
        if m and needle in m.group(1).strip().lower():
            return part.strip()
    return ""


def _compact_tool_bullets(body: str, budget: int) -> str:
    """One line per ### `tool` block: tool name + first bullet."""
    lines: list[str] = []
    current_tool = ""
    for line in body.splitlines():
        m = re.match(r"^### `([^`]+)`", line)
        if m:
            current_tool = m.group(1)
            continue
        if current_tool and line.strip().startswith("- Use"):
            lines.append(f"- `{current_tool}`: {line.strip()[2:].strip()}")
            current_tool = ""
        if sum(len(x) + 1 for x in lines) >= budget:
            break
    return "\n".join(lines)


def reload_static_routing_cache() -> None:
    global _cache_mtime, _cache_body
    _cache_mtime = None
    _cache_body = ""


def get_static_tool_routing(max_chars: int = DEFAULT_STATIC_MAX_CHARS) -> str:
    """
    Return deterministic static routing text capped at *max_chars*.

    Priority: Quick Routing Rules section, then compact per-tool bullets.
    """
    global _cache_mtime, _cache_body

    if not _STATIC_PATH.exists():
        return ""

    mtime = _STATIC_PATH.stat().st_mtime
    if _cache_mtime != mtime:
        raw = _STATIC_PATH.read_text(encoding="utf-8")
        _cache_body = _strip_frontmatter(raw)
        _cache_mtime = mtime

    body = _cache_body
    if len(body) <= max_chars:
        return body

    header = "### TOOL ROUTING (static) ###\n"
    quick = _extract_section(body, "Quick Routing Rules")
    if quick:
        content = f"{header}{quick}"
        if len(content) <= max_chars:
            return content
        return content[:max_chars]

    bullets = _compact_tool_bullets(body, max_chars - len(header) - 20)
    if bullets:
        content = f"{header}{bullets}"
        return content[:max_chars]

    return body[:max_chars]
