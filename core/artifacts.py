"""Resolve known project artifacts and search for files by name."""

from __future__ import annotations

import fnmatch
from pathlib import Path

from core import runtime_paths as _runtime_paths

_SKIP_PARTS = {".venv", "__pycache__", "artifacts/archived"}

# Canonical relative locations (under each search root)
_PCAP_SEARCH_DIRS = ("", "workspace", "artifacts/captures")


def _score_match(rel_path: str, query_name: str) -> int:
    """Higher score means better recommendation ranking."""
    rel = rel_path.replace("\\", "/")
    lower = rel.lower()
    name = (query_name or "").strip().lower()
    score = 0

    # Prefer operational artifacts over playbooks/docs.
    if lower.startswith("output/") and "report_" in lower:
        score += 80
    if lower.startswith(".pulse/pcap_logs/") or "/.pulse/pcap_logs/" in lower:
        score += 70
    if lower.startswith("workspace/"):
        score += 50
    if lower.startswith("artifacts/captures/"):
        score += 40
    if lower.startswith("knowledge/"):
        score -= 40
    if lower.startswith("docs/"):
        score -= 30

    # Exact basename > wildcard/partial.
    basename = Path(lower).name
    pattern = Path(name).name if name else ""
    if pattern and not any(ch in pattern for ch in "*?[]"):
        if basename == pattern:
            score += 25
    elif pattern and fnmatch.fnmatch(basename, pattern):
        score += 20
    elif pattern and pattern in basename:
        score += 10

    # Fresh report filenames (timestamped) likely more useful.
    if "report_" in basename:
        score += 5

    return score


def resolve_project_file(name: str) -> Path | None:
    """Return first existing path for a filename under known project locations."""
    candidate = Path(name)
    if candidate.is_absolute() and candidate.is_file():
        return candidate.resolve()

    basename = candidate.name if candidate.name else name
    rel_parts = candidate.parts

    for root in _runtime_paths.search_roots():
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
    roots = (search_root.resolve(),) if search_root else _runtime_paths.search_roots()
    basename = Path(name).name
    if not basename:
        return {"success": False, "error": "No filename provided.", "matches": []}
    pattern = basename

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
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if any(skip in str(path) for skip in (".venv", "artifacts\\archived", "artifacts/archived")):
                continue
            if not fnmatch.fnmatch(path.name, pattern):
                continue
            try:
                rel = str(path.relative_to(root)).replace("\\", "/")
            except ValueError:
                rel = str(path).replace("\\", "/")
            if rel not in matches:
                matches.append(rel)

    # Rank recommendations: keep full match list for visibility, but suggest best operational path.
    ranked = sorted(matches, key=lambda p: _score_match(p, pattern), reverse=True)
    ranked = _filter_visible_matches(ranked)
    recommended = ranked[0] if ranked else None

    if not ranked:
        err = f"No files matching pattern '{pattern}'."
        if any(ch in pattern for ch in "*?[]"):
            err += (
                " Wildcard patterns match file NAMES only, not file contents. "
                "To search inside files use grep_file/find_and_grep; to broaden the "
                "name search try a less specific pattern or a different directory."
            )
        # #region agent log
        try:
            from core.debug_log import debug_log
            debug_log(
                "core/artifacts.py:find_file",
                "no matches",
                {"pattern": pattern, "wildcard": any(ch in pattern for ch in "*?[]")},
                "H1",
            )
        except Exception:
            pass
        # #endregion
        return {"success": False, "error": err, "matches": [], "recommended": None}

    return {
        "success": True,
        "matches": ranked[:20],
        "recommended": recommended,
    }


def _filter_visible_matches(matches: list[str]) -> list[str]:
    try:
        from core.session_visibility import filter_session_paths

        return filter_session_paths(matches)
    except Exception:
        return matches
