"""Read editorial findings from the neutral feed directory (Editorial Anarcoiris)."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def resolve_feed_dir() -> Path:
    raw = os.environ.get("EDITORIAL_FEED_DIR", "")
    if not raw:
        raw = str(Path.home() / "Documents" / "Libraries" / "editorial-feed")
    return Path(raw).resolve()


def _load_manifest(feed_dir: Path) -> dict:
    manifest_path = feed_dir / "manifest.json"
    if not manifest_path.exists():
        return {"findings": []}
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"findings": []}


def _excerpt_from_chunk(chunk_path: Path, max_chars: int = 400) -> str:
    if not chunk_path.exists():
        return ""
    text = chunk_path.read_text(encoding="utf-8", errors="ignore")
    m = _FRONTMATTER_RE.match(text)
    body = text[m.end():] if m else text
    body = body.strip()
    return body[:max_chars] + ("..." if len(body) > max_chars else "")


def editorial_lookup(
    query: str = "",
    finding_id: str | None = None,
    project_slug: str | None = None,
    max_results: int = 5,
) -> dict[str, Any]:
    """Search the editorial feed for triage/proposal findings."""
    feed_dir = resolve_feed_dir()
    if not feed_dir.exists():
        return {
            "success": False,
            "error": (
                f"Editorial feed not found at {feed_dir}. "
                "Run export_editorial_feed.py from Editorial Anarcoiris first."
            ),
            "feed_dir": str(feed_dir),
            "results": [],
        }

    manifest = _load_manifest(feed_dir)
    entries = manifest.get("findings", [])
    query_l = query.lower().strip()
    fid_l = (finding_id or "").strip().upper()
    slug_l = (project_slug or "").lower().strip()

    matched: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        eid = str(entry.get("finding_id") or entry.get("id") or "").upper()
        if fid_l and eid != fid_l:
            continue
        if slug_l and slug_l not in str(entry.get("project_slug", "")).lower():
            continue
        if query_l:
            hay = " ".join(
                str(entry.get(k, "")) for k in ("title", "action", "issue_type", "finding_id")
            ).lower()
            if query_l not in hay:
                continue
        chunk = entry.get("chunk_path") or entry.get("path") or ""
        excerpt = ""
        if chunk:
            excerpt = _excerpt_from_chunk(feed_dir / str(chunk))
        matched.append({
            "finding_id": eid or entry.get("finding_id"),
            "project_slug": entry.get("project_slug"),
            "priority": entry.get("priority"),
            "title": entry.get("title"),
            "action": entry.get("action"),
            "excerpt": excerpt,
        })
        if len(matched) >= max_results:
            break

    return {
        "success": True,
        "feed_dir": str(feed_dir),
        "count": len(matched),
        "results": matched,
    }
