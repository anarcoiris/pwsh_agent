"""Session-scoped workspace and state path helpers."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.runtime_paths import app_root

_ACTIVE_FILE = "active_session.json"
_LEGACY_SESSION = "default"


def generate_session_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def session_start_iso(session_id: str) -> str | None:
    """Parse ``YYYYMMDD_HHMMSS`` session ids into an ISO-8601 UTC timestamp."""
    m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})", (session_id or "").strip())
    if not m:
        return None
    y, mo, d, h, mi, s = m.groups()
    return f"{y}-{mo}-{d}T{h}:{mi}:{s}+00:00"


def sessions_state_root() -> Path:
    return app_root() / "state" / "sessions"


def sessions_workspace_root() -> Path:
    return app_root() / "workspace" / "sessions"


def active_session_meta_path() -> Path:
    return app_root() / "state" / _ACTIVE_FILE


def load_active_session_id() -> str:
    path = active_session_meta_path()
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            sid = str(data.get("session_id", "")).strip()
            if sid:
                return sid
        except (OSError, json.JSONDecodeError):
            pass
    return _LEGACY_SESSION


def save_active_session(session_id: str, *, previous: str | None = None) -> None:
    path = active_session_meta_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "session_id": session_id,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if previous:
        payload["previous"] = previous
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def session_state_dir(session_id: str) -> Path:
    return sessions_state_root() / session_id


def session_workspace_dir(session_id: str) -> Path:
    return sessions_workspace_root() / session_id


def session_artifacts_dir(session_id: str) -> Path:
    return session_state_dir(session_id) / "artifacts"


def facts_file(session_id: str) -> Path:
    return session_state_dir(session_id) / "facts.json"


def plan_state_file(session_id: str) -> Path:
    return session_state_dir(session_id) / "plan_state.json"


def intent_spec_file(session_id: str) -> Path:
    return session_state_dir(session_id) / "intent_spec.json"


def facts_rel(session_id: str) -> str:
    return _rel(facts_file(session_id))


def plan_file(session_id: str) -> Path:
    return session_workspace_dir(session_id) / f"plan_{session_id}.md"


def status_file(session_id: str) -> Path:
    return session_workspace_dir(session_id) / f"status_{session_id}.md"


def session_log_file(session_id: str) -> Path:
    return session_workspace_dir(session_id) / f"session_log_{session_id}.md"


def scratchpads_dir(session_id: str) -> Path:
    return session_workspace_dir(session_id) / "scratchpads"


def scratchpad_file(session_id: str, task_id: str) -> Path:
    safe = re.sub(r"[^\w\-]+", "_", task_id.strip()) or "task"
    return scratchpads_dir(session_id) / f"{safe}.md"


def plan_note_rel(session_id: str) -> str:
    return _rel(plan_file(session_id))


def status_note_rel(session_id: str) -> str:
    return _rel(status_file(session_id))


def session_log_rel(session_id: str) -> str:
    return _rel(session_log_file(session_id))


def scratchpad_rel(session_id: str, task_id: str) -> str:
    return _rel(scratchpad_file(session_id, task_id))


def _rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(app_root().resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def ensure_session_layout(session_id: str) -> dict[str, str]:
    """Create session dirs and seed plan/status headers if missing."""
    ws = session_workspace_dir(session_id)
    ws.mkdir(parents=True, exist_ok=True)
    scratchpads_dir(session_id).mkdir(parents=True, exist_ok=True)
    session_state_dir(session_id).mkdir(parents=True, exist_ok=True)
    session_artifacts_dir(session_id).mkdir(parents=True, exist_ok=True)

    paths = {
        "plan": plan_note_rel(session_id),
        "status": status_note_rel(session_id),
        "session_log": session_log_rel(session_id),
        "workspace": _rel(ws),
    }

    for kind, p in (
        ("plan", plan_file(session_id)),
        ("status", status_file(session_id)),
        ("session_log", session_log_file(session_id)),
    ):
        if not p.is_file():
            title = kind.replace("_", " ").title()
            p.write_text(
                f"# {title} — session {session_id}\n\n",
                encoding="utf-8",
            )
    return paths


def list_session_ids() -> list[str]:
    ids: set[str] = set()
    for root in (sessions_state_root(), sessions_workspace_root()):
        if root.is_dir():
            for child in root.iterdir():
                if child.is_dir():
                    ids.add(child.name)
    if not ids:
        ids.add(_LEGACY_SESSION)
    return sorted(ids, reverse=True)


def normalize_note_path(path: str, session_id: str) -> str:
    """Map legacy workspace/plan.md → session-scoped plan_{id}.md."""
    normalized = path.replace("\\", "/").strip()
    lower = normalized.lower()
    legacy_map = {
        "workspace/plan.md": plan_note_rel(session_id),
        "workspace/status.md": status_note_rel(session_id),
        "workspace/session_log.md": session_log_rel(session_id),
        "plan.md": plan_note_rel(session_id),
        "status.md": status_note_rel(session_id),
        "session_log.md": session_log_rel(session_id),
    }
    if lower in legacy_map:
        return legacy_map[lower]
    return normalized


def is_session_note_path(path: str, session_id: str | None = None) -> bool:
    name = Path(path.replace("\\", "/")).name.lower()
    if re.match(r"^(plan|status|session_log)_[\w]+\.md$", name):
        return True
    if name in ("plan.md", "status.md", "session_log.md"):
        return True
    if "/scratchpads/" in path.replace("\\", "/").lower() and name.endswith(".md"):
        return True
    return False


def list_prior_artifact_snippets(max_sessions: int = 3, max_chars: int = 1200) -> str:
    """Heads of plan/status/scratchpads from prior sessions (newest first)."""
    parts: list[str] = []
    active = load_active_session_id()
    for sid in list_session_ids():
        if sid == active:
            continue
        if len([p for p in parts if p.startswith("--- session")]) >= max_sessions:
            break
        for label, fp in (
            ("plan", plan_file(sid)),
            ("status", status_file(sid)),
        ):
            if fp.is_file():
                try:
                    text = fp.read_text(encoding="utf-8", errors="replace")[:350]
                    parts.append(f"--- session {sid} {label} ---\n{text}")
                except OSError:
                    pass
        sp_dir = scratchpads_dir(sid)
        if sp_dir.is_dir():
            for sp in sorted(sp_dir.glob("*.md"))[:2]:
                try:
                    text = sp.read_text(encoding="utf-8", errors="replace")[:250]
                    parts.append(f"--- session {sid} scratchpad/{sp.name} ---\n{text}")
                except OSError:
                    pass
        if sum(len(p) for p in parts) > max_chars:
            break
    if not parts:
        return ""
    header = "[PRIOR SESSIONS — read only when user asks for earlier work]\n"
    return header + "\n\n".join(parts)[:max_chars]
