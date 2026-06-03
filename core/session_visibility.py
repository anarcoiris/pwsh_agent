"""Session-scoped path visibility — hide other sessions unless explicitly unlocked."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from core.runtime_paths import app_root, search_roots

_SESSION_ID_RE = re.compile(r"\b(\d{8}_\d{6})\b")
_SESSION_PICK_RE = re.compile(
    r"@session[:\s]+(\d{8}_\d{6})|session\s+pick\s+(\d{8}_\d{6})|continue\s+session\s+(\d{8}_\d{6})",
    re.I,
)

_CTX: dict[str, Any] = {
    "active_id": "",
    "unlocked": set(),
    "fence_enabled": False,
}


def set_visibility_context(
    *,
    active_session_id: str,
    unlocked: set[str] | None = None,
    fence_enabled: bool = False,
) -> None:
    _CTX["active_id"] = (active_session_id or "").strip()
    _CTX["unlocked"] = set(unlocked or set())
    _CTX["fence_enabled"] = bool(fence_enabled)


def clear_visibility_context() -> None:
    _CTX["active_id"] = ""
    _CTX["unlocked"] = set()
    _CTX["fence_enabled"] = False


def allowed_session_ids() -> set[str]:
    active = (_CTX.get("active_id") or "").strip()
    allowed = set(_CTX.get("unlocked") or set())
    if active:
        allowed.add(active)
    return allowed


def unlock_from_message(text: str) -> set[str]:
    out: set[str] = set()
    for m in _SESSION_ID_RE.finditer(text or ""):
        out.add(m.group(1))
    for m in _SESSION_PICK_RE.finditer(text or ""):
        for g in m.groups():
            if g:
                out.add(g)
    return out


def _session_id_in_path(path: str) -> str | None:
    norm = (path or "").replace("\\", "/").lower()
    for part in Path(norm).parts:
        if _SESSION_ID_RE.fullmatch(part):
            return part
    m = re.search(r"/sessions/(\d{8}_\d{6})", norm)
    if m:
        return m.group(1)
    return None


def is_path_visible(path: str | Path) -> bool:
    if not _CTX.get("fence_enabled"):
        return True
    sid = _session_id_in_path(str(path))
    if not sid:
        return True
    return sid in allowed_session_ids()


def path_visibility_error(path: str | Path) -> str | None:
    if is_path_visible(path):
        return None
    sid = _session_id_in_path(str(path)) or "?"
    active = _CTX.get("active_id") or "?"
    return (
        f"Path '{path}' is in session {sid} which is not active/unlocked. "
        f"Active session: {active}. Use console 'session pick {sid}' or reference that session in your message."
    )


def filter_session_paths(paths: list[str]) -> list[str]:
    if not _CTX.get("fence_enabled"):
        return paths
    return [p for p in paths if is_path_visible(p)]


def is_under_other_session_dir(path: Path) -> bool:
    """True if path is under workspace/sessions/X or state/sessions/X for non-allowed X."""
    if not _CTX.get("fence_enabled"):
        return False
    try:
        resolved = path.resolve()
    except OSError:
        return False
    allowed = allowed_session_ids()
    for root in search_roots():
        for sub in ("workspace/sessions", "state/sessions"):
            base = (root / sub.replace("/", "\\")).resolve() if "\\" in str(root) else (root / sub).resolve()
            if not base.is_dir():
                continue
            try:
                resolved.relative_to(base)
            except ValueError:
                continue
            parts = resolved.relative_to(base).parts
            if parts:
                sid = parts[0]
                if _SESSION_ID_RE.fullmatch(sid) and sid not in allowed:
                    return True
    return False
