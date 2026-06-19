"""Read hygiene findings from the neutral feed directory (repo-hygiene eyes)."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import yaml

from core.runtime_paths import app_root

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _load_agent_config() -> dict:
    cfg_path = app_root() / "config.yaml"
    if not cfg_path.exists():
        return {}
    with open(cfg_path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def resolve_feed_dir() -> Path:
    config = _load_agent_config()
    eyes = config.get("hygiene_eyes", {})
    raw = os.environ.get("HYGIENE_FEED_DIR") or eyes.get("feed_dir", "")
    if not raw:
        raw = str(app_root().parent / "hygiene-feed")
    return Path(raw).resolve()


def _load_manifest(feed_dir: Path) -> dict:
    manifest_path = feed_dir / "manifest.json"
    if not manifest_path.exists():
        return {"findings": []}
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return {"findings": []}


def _excerpt_from_chunk(chunk_path: Path, max_chars: int = 400) -> str:
    if not chunk_path.exists():
        return ""
    text = chunk_path.read_text(encoding="utf-8", errors="ignore")
    m = _FRONTMATTER_RE.match(text)
    body = text[m.end():] if m else text
    body = body.strip()
    return body[:max_chars] + ("..." if len(body) > max_chars else "")


def hygiene_lookup(
    query: str = "",
    repo: str | None = None,
    severity: str | None = None,
    finding_id: str | None = None,
    auto_fixable_only: bool = False,
    max_results: int = 5,
) -> dict[str, Any]:
    """Search the hygiene feed for findings matching filters."""
    feed_dir = resolve_feed_dir()
    if not feed_dir.exists():
        return {
            "success": False,
            "error": f"Hygiene feed not found at {feed_dir}. Run repo-hygiene export_feed.py first.",
            "feed_dir": str(feed_dir),
            "results": [],
        }

    manifest = _load_manifest(feed_dir)
    entries = manifest.get("findings", [])
    query_l = query.lower().strip()
    fid_l = (finding_id or "").strip().upper()
    repo_l = (repo or "").lower().strip()
    sev = (severity or "").upper().strip()

    matched: list[dict[str, Any]] = []
    for entry in entries:
        if fid_l and entry.get("finding_id", "").upper() != fid_l:
            continue
        if repo_l and entry.get("repo", "").lower() != repo_l:
            continue
        if sev and entry.get("severity", "").upper() != sev:
            continue
        if auto_fixable_only and not entry.get("auto_fixable"):
            continue
        if query_l:
            hay = " ".join(
                str(entry.get(k, ""))
                for k in ("finding_id", "title", "file", "task_id", "source")
            ).lower()
            if query_l not in hay:
                continue

        chunk_rel = entry.get("chunk_path", "")
        chunk_path = feed_dir / chunk_rel if chunk_rel else None
        excerpt = _excerpt_from_chunk(chunk_path) if chunk_path else entry.get("title", "")

        matched.append({
            "finding_id": entry.get("finding_id"),
            "repo": entry.get("repo"),
            "severity": entry.get("severity"),
            "auto_fixable": entry.get("auto_fixable", False),
            "title": entry.get("title"),
            "file": entry.get("file"),
            "line": entry.get("line"),
            "task_id": entry.get("task_id"),
            "source": entry.get("source"),
            "excerpt": excerpt,
        })

    matched = matched[: max(1, min(max_results, 20))]
    return {
        "success": True,
        "feed_dir": str(feed_dir),
        "count": len(matched),
        "results": matched,
    }
