"""Resolve known project artifacts and search for files by name."""

from __future__ import annotations

from pathlib import Path

from core.runtime_paths import workspace_root

_SKIP_PARTS = {".venv", "__pycache__", "artifacts/archived"}

# Canonical locations post-cleanup (search order for known PCAP names)
_PCAP_SEARCH_DIRS = ("", "workspace", "artifacts/captures")


def resolve_project_file(name: str) -> Path | None:
    """Return first existing path for a filename under known project locations."""
    root = workspace_root()
    candidate = Path(name)
    if candidate.is_absolute() and candidate.is_file():
        return candidate.resolve()

    basename = candidate.name if candidate.name else name
    rel_parts = candidate.parts

    if len(rel_parts) > 1:
        direct = (root / Path(*rel_parts)).resolve()
        if direct.is_file():
            return direct
        return None

    for sub in _PCAP_SEARCH_DIRS:
        base = root / sub if sub else root
        hit = (base / basename).resolve()
        if hit.is_file():
            return hit

    return None


def find_file(name: str, search_root: Path | None = None) -> dict:
    """Search project tree for files matching basename (skips .venv and archived backups)."""
    root = search_root or workspace_root()
    basename = Path(name).name
    if not basename:
        return {"success": False, "error": "No filename provided.", "matches": []}

    known = resolve_project_file(basename)
    matches: list[str] = []
    if known:
        matches.append(str(known.relative_to(root)).replace("\\", "/"))

    for path in root.rglob(basename):
        parts = path.parts
        if any(skip in str(path) for skip in (".venv", "artifacts\\archived", "artifacts/archived")):
            continue
        rel = str(path.relative_to(root)).replace("\\", "/")
        if rel not in matches:
            matches.append(rel)

    return {
        "success": bool(matches),
        "matches": matches[:20],
        "recommended": matches[0] if matches else None,
    }
