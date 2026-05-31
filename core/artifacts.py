"""Resolve known project artifacts and search for files by name."""

from __future__ import annotations

from pathlib import Path

from core.runtime_paths import search_roots, workspace_root

_SKIP_PARTS = {".venv", "__pycache__", "artifacts/archived"}

# Canonical relative locations (under each search root)
_PCAP_SEARCH_DIRS = ("", "workspace", "artifacts/captures")


def resolve_project_file(name: str) -> Path | None:
    """Return first existing path for a filename under known project locations."""
    candidate = Path(name)
    if candidate.is_absolute() and candidate.is_file():
        return candidate.resolve()

    basename = candidate.name if candidate.name else name
    rel_parts = candidate.parts

    for root in search_roots():
        if len(rel_parts) > 1:
            direct = (root / Path(*rel_parts)).resolve()
            if direct.is_file():
                # #region agent log
                try:
                    from core.debug_log import debug_log
                    debug_log(
                        "core/artifacts.py:resolve_project_file",
                        "hit relative path",
                        {"input": name, "root": str(root), "resolved": str(direct)},
                        "C",
                    )
                except Exception:
                    pass
                # #endregion
                return direct
            continue

        for sub in _PCAP_SEARCH_DIRS:
            base = root / sub if sub else root
            hit = (base / basename).resolve()
            if hit.is_file():
                # #region agent log
                try:
                    from core.debug_log import debug_log
                    debug_log(
                        "core/artifacts.py:resolve_project_file",
                        "hit canonical subdir",
                        {"input": name, "root": str(root), "subdir": sub, "resolved": str(hit)},
                        "C",
                    )
                except Exception:
                    pass
                # #endregion
                return hit

    return None


def find_file(name: str, search_root: Path | None = None) -> dict:
    """Search project tree for files matching basename (skips .venv and archived backups)."""
    roots = (search_root.resolve(),) if search_root else search_roots()
    basename = Path(name).name
    if not basename:
        return {"success": False, "error": "No filename provided.", "matches": []}

    known = resolve_project_file(basename)
    matches: list[str] = []
    display_root = roots[0]

    if known:
        for root in roots:
            try:
                matches.append(str(known.relative_to(root)).replace("\\", "/"))
                display_root = root
                break
            except ValueError:
                continue
        if not matches:
            matches.append(str(known).replace("\\", "/"))

    for root in roots:
        for path in root.rglob(basename):
            if any(skip in str(path) for skip in (".venv", "artifacts\\archived", "artifacts/archived")):
                continue
            try:
                rel = str(path.relative_to(root)).replace("\\", "/")
            except ValueError:
                rel = str(path).replace("\\", "/")
            if rel not in matches:
                matches.append(rel)

    return {
        "success": bool(matches),
        "matches": matches[:20],
        "recommended": matches[0] if matches else None,
    }
