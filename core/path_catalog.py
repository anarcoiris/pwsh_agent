"""
core/path_catalog.py — Canonical artifact locations (single source of truth).

Layout:
  state/sessions/{id}/     — agent JSON history, facts, context_dump
  workspace/sessions/{id}/ — plan_{id}.md, status_{id}.md, scratchpads (NOT workspace/plan.md)
  output/reports/          — engagement report_*.md
  .pulse/pcap_logs/        — verbose PCAP decode logs
  workspace/               — deliverables (pwd.txt, login_forms.txt, last_capture.pcapng)
  artifacts/captures/      — PCAP copies
"""

from __future__ import annotations

from pathlib import Path

from core.runtime_paths import app_root, search_roots, workspace_root
from core.session_paths import facts_file, plan_file, status_file, session_workspace_dir

LEGACY_SESSION_NOTE_PATHS = frozenset({
    "workspace/plan.md",
    "workspace/status.md",
    "workspace/session_log.md",
    "plan.md",
    "status.md",
    "session_log.md",
})

REPORT_GLOB = "report_*.md"
PCAP_LOG_GLOB = "verbose_*.txt"
DEFAULT_PCAP = "last_capture.pcapng"


def pcap_logs_dir() -> Path:
    return app_root() / ".pulse" / "pcap_logs"


def report_dirs() -> list[Path]:
    dirs: list[Path] = []
    for root in search_roots():
        for sub in ("output/reports", "output", "reports", "workspace/reports"):
            d = root / sub.replace("/", "\\") if "\\" in str(root) else root / sub
            if d.is_dir():
                dirs.append(d)
    return dirs


def latest_reports(limit: int = 2) -> list[Path]:
    found: list[Path] = []
    for d in report_dirs():
        found.extend(sorted(d.glob(REPORT_GLOB), reverse=True))
    seen: set[str] = set()
    unique: list[Path] = []
    for p in sorted(found, key=lambda x: x.stat().st_mtime if x.exists() else 0, reverse=True):
        key = str(p.resolve())
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique[:limit]


def latest_pcap_verbose_log() -> Path | None:
    d = pcap_logs_dir()
    if not d.is_dir():
        return None
    logs = sorted(d.glob(PCAP_LOG_GLOB), reverse=True)
    return logs[0] if logs else None


def session_context_paths(session_id: str) -> list[tuple[str, Path]]:
    """Ordered (label, path) for ephemeral session injection — no legacy workspace/plan.md."""
    sid = session_id
    out: list[tuple[str, Path]] = [
        ("session_plan", plan_file(sid)),
        ("session_status", status_file(sid)),
        ("session_facts", facts_file(sid)),
    ]
    for i, rp in enumerate(latest_reports(2)):
        out.append((f"report_{i}", rp))
    vlog = latest_pcap_verbose_log()
    if vlog:
        out.append(("pcap_verbose_log", vlog))
    ws = session_workspace_dir(sid)
    for name in ("login_forms.txt", "pwd.txt"):
        p = ws / name
        if p.is_file():
            out.append((name, p))
    for root in search_roots():
        for name in ("login_forms.txt", "pwd.txt"):
            p = root / "workspace" / name
            if p.is_file():
                out.append((name, p))
    return out


def rel_path(path: Path) -> str:
    for root in search_roots():
        try:
            return path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            continue
    return path.as_posix()


def resolve_read_target(path: str) -> tuple[Path | None, str | None]:
    """
    Resolve a read_file path; expand globs via find_file.
    Returns (resolved_path, error_message).
    """
    from core.artifacts import find_file, resolve_project_file

    raw = (path or "").strip()
    if not raw:
        return None, "No path provided."

    if any(ch in raw for ch in "*?[]"):
        res = find_file(raw)
        if res.get("recommended"):
            hit = resolve_project_file(res["recommended"])
            if hit:
                return hit, None
        return None, res.get("error") or f"No file matched pattern '{raw}'."

    hit = resolve_project_file(raw)
    if hit:
        return hit, None
    p = Path(raw)
    if p.is_file():
        return p.resolve(), None
    return None, f"File '{raw}' does not exist. Use find_file('report_*.md') then read_file the recommended path."


def deliverable_hint_block(session_id: str, deliverables: list[str]) -> str:
    lines = [
        "[ARTIFACT PATHS — use these, not legacy workspace/plan.md]",
        f"Session plan: {plan_file(session_id).as_posix()}",
        f"Session status: {status_file(session_id).as_posix()}",
    ]
    reps = latest_reports(1)
    if reps:
        lines.append(f"Latest report: {rel_path(reps[0])}")
    vlog = latest_pcap_verbose_log()
    if vlog:
        lines.append(f"Latest PCAP verbose log: {rel_path(vlog)}")
    if deliverables:
        lines.append(f"Deliverable(s): {', '.join(deliverables)}")
    ws = session_workspace_dir(session_id)
    lines.append(f"Session workspace: {ws.as_posix()}")
    lines.append(
        "read_file and grep_file accept globs (e.g. report_*.md, verbose_*.txt) — resolved via find_file ranking. "
        "Prefer explicit paths from find_file.recommended, SESSION FACTS, or analyze_pcapng when known."
    )
    return "\n".join(lines)


_SESSION_DELIVERABLE_NAMES = frozenset({
    "pwd.txt",
    "login_forms.txt",
    "last_capture.pcapng",
})


def resolve_write_target(
    path: str,
    session_id: str,
    deliverables: list[str] | None = None,
) -> Path:
    """
    Resolve write_file destination; route bare deliverable names to session workspace.
    """
    from core.session_paths import normalize_note_path

    raw = (path or "").strip()
    if not raw:
        return Path(path).resolve()

    normalized = normalize_note_path(raw, session_id).replace("\\", "/")
    basename = Path(normalized).name

    deliverable_set = {Path(d).name.lower() for d in (deliverables or [])}
    if basename.lower() in _SESSION_DELIVERABLE_NAMES or basename.lower() in deliverable_set:
        if not Path(normalized).is_absolute() and "/" not in normalized.replace("\\", "/").strip("/"):
            return (session_workspace_dir(session_id) / basename).resolve()

    p = Path(normalized)
    if p.is_absolute():
        return p.resolve()
    return p.resolve()
