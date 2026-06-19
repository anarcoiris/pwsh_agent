"""MVF (Minimum Verifiable Footprint) — deterministic session validation on CPU."""

from __future__ import annotations

import json
import logging
import re
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.runtime_paths import app_root

logger = logging.getLogger("pwsh_agent.core.mvf_validator")

_PYTEST_RE = re.compile(r"\bpytest\b", re.I)
_TESTS_PATH_RE = re.compile(r"\btests?/", re.I)
_DELIVERABLE_RE = re.compile(
    r"(?:[A-Za-z][A-Za-z0-9_-]*/)?[A-Za-z][A-Za-z0-9_-]*\.(?:py|md|ps1|js|ts|yaml|yml|json)",
)
_DIR_NAME_RE = re.compile(
    r"(?:director(?:y|io)|folder|carpeta|directorio)\s+(?:llamado|named|called)?\s*['\"]?([A-Za-z0-9_.-]+)/?['\"]?",
    re.I,
)
_BARE_DIR_RE = re.compile(r"\b([A-Za-z][A-Za-z0-9_-]+)/\b")
_COUNT_FILES_RE = re.compile(
    r"\b(\d+)\s+(?:relatos|archivos|files|markdown(?:\s+files)?)\b",
    re.I,
)


@dataclass
class MvfCheckResult:
    type: str
    ok: bool
    detail: str = ""
    path: str | None = None
    duration_ms: int = 0


@dataclass
class MvfResult:
    validated: bool
    checks: list[MvfCheckResult] = field(default_factory=list)


def mvf_path(session_id: str) -> Path:
    return app_root() / "state" / "sessions" / session_id / "mvf.json"


def load_mvf(session_id: str) -> dict[str, Any] | None:
    path = mvf_path(session_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Could not load mvf.json for %s: %s", session_id, e)
        return None


def save_mvf(session_id: str, data: dict[str, Any]) -> None:
    path = mvf_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_checks(checks: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in checks or []:
        if isinstance(raw, dict) and raw.get("type"):
            out.append(dict(raw))
    return out


def merge_mvf_override(base: dict[str, Any], override: dict[str, Any] | None) -> dict[str, Any]:
    if not override:
        return base
    merged = dict(base)
    if override.get("deliverables"):
        merged["deliverables"] = list(override["deliverables"])
    if override.get("checks"):
        merged["checks"] = _normalize_checks(override["checks"])
    merged["derived_from"] = "template_override"
    return merged


def _dedupe_paths(paths: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for p in paths:
        s = str(p).strip().replace("\\", "/")
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _paths_from_mission_text(mission_text: str) -> list[str]:
    paths: list[str] = []
    for match in _DELIVERABLE_RE.findall(mission_text or ""):
        s = str(match).strip().replace("\\", "/")
        if s and s not in paths:
            paths.append(s)
    return paths


def _dirs_from_mission_text(mission_text: str) -> list[str]:
    dirs: list[str] = []
    for m in _DIR_NAME_RE.finditer(mission_text or ""):
        d = (m.group(1) or "").strip().rstrip("/")
        if d and d not in dirs:
            dirs.append(d)
    for m in _BARE_DIR_RE.finditer(mission_text or ""):
        d = (m.group(1) or "").strip()
        if d and d not in dirs:
            dirs.append(d)
    return dirs


def _expected_file_count(mission_text: str) -> int | None:
    m = _COUNT_FILES_RE.search(mission_text or "")
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def _run_dir_exists_check(check: dict[str, Any], root: Path) -> MvfCheckResult:
    rel = str(check.get("path", "")).strip().rstrip("/")
    target = _resolve_path(rel, root)
    t0 = time.perf_counter()
    ok = target.is_dir()
    ms = int((time.perf_counter() - t0) * 1000)
    return MvfCheckResult(
        type="dir_exists",
        ok=ok,
        path=rel,
        detail="ok" if ok else f"missing dir: {rel}",
        duration_ms=ms,
    )


def _run_dir_count_check(check: dict[str, Any], root: Path) -> MvfCheckResult:
    rel = str(check.get("path", "")).strip().rstrip("/")
    pattern = str(check.get("glob", "*")).strip() or "*"
    min_count = int(check.get("min_count", 1))
    target = _resolve_path(rel, root)
    t0 = time.perf_counter()
    if not target.is_dir():
        ms = int((time.perf_counter() - t0) * 1000)
        return MvfCheckResult(
            type="dir_count",
            ok=False,
            path=rel,
            detail=f"missing dir: {rel}",
            duration_ms=ms,
        )
    count = sum(1 for _ in target.glob(pattern))
    ms = int((time.perf_counter() - t0) * 1000)
    ok = count >= min_count
    return MvfCheckResult(
        type="dir_count",
        ok=ok,
        path=rel,
        detail=f"{count}/{min_count} matches '{pattern}'" if ok else f"only {count}/{min_count} in {rel}",
        duration_ms=ms,
    )


def _default_pytest_cmd(deliverables: list[str], mission_text: str) -> str | None:
    for p in deliverables:
        norm = p.replace("\\", "/")
        if "/tests/" in norm or norm.startswith("tests/"):
            parts = norm.split("/")
            if len(parts) >= 2 and parts[0]:
                return f"py -3.10 -m pytest {parts[0]}/tests -q"
    m = _TESTS_PATH_RE.search(mission_text or "")
    if m or _PYTEST_RE.search(mission_text or ""):
        for p in deliverables:
            top = p.split("/")[0].split("\\")[0]
            if top:
                return f"py -3.10 -m pytest {top}/tests -q"
    return None


def derive_mvf_from_intent(
    spec: Any | None,
    mission_text: str,
    *,
    override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build mvf.json payload from IntentSpec + mission heuristics."""
    deliverables: list[str] = []
    if spec is not None:
        deliverables.extend(getattr(spec, "deliverables", None) or [])
        if hasattr(spec, "to_dict"):
            deliverables.extend((spec.to_dict() or {}).get("deliverables") or [])

    deliverables.extend(_paths_from_mission_text(mission_text))
    deliverables = _dedupe_paths(deliverables)

    dirs = _dirs_from_mission_text(mission_text)
    expected_n = _expected_file_count(mission_text)

    checks: list[dict[str, Any]] = []
    for path in deliverables:
        checks.append({"type": "file_exists", "path": path})

    for d in dirs:
        checks.append({"type": "dir_exists", "path": d})
        if expected_n and expected_n > 1:
            checks.append({
                "type": "dir_count",
                "path": d,
                "glob": "*.md",
                "min_count": expected_n,
            })

    pytest_cmd = _default_pytest_cmd(deliverables, mission_text)
    if pytest_cmd:
        checks.append({"type": "command", "cmd": pytest_cmd, "exit_code": 0, "cwd": None})

    domain = getattr(spec, "domain", None) if spec else None
    if domain == "code_build" and not pytest_cmd and deliverables:
        top_dirs = {p.split("/")[0] for p in deliverables if "/" in p}
        for top in sorted(top_dirs):
            checks.append({
                "type": "command",
                "cmd": f"py -3.10 -m pytest {top}/tests -q",
                "exit_code": 0,
                "cwd": None,
            })
            break

    data: dict[str, Any] = {
        "deliverables": deliverables,
        "checks": checks,
        "validated": False,
        "last_run_at": None,
        "last_results": [],
        "derived_from": "intent_spec",
    }
    return merge_mvf_override(data, override)


def _resolve_path(path: str, root: Path) -> Path:
    p = Path(path)
    if p.is_absolute():
        return p
    return (root / p).resolve()


def _run_command_check(check: dict[str, Any], root: Path) -> MvfCheckResult:
    cmd_raw = check.get("cmd")
    if not cmd_raw:
        return MvfCheckResult(type="command", ok=False, detail="missing cmd")
    expected = int(check.get("exit_code", 0))
    cwd_raw = check.get("cwd")
    cwd = root if cwd_raw is None else Path(str(cwd_raw))
    if not cwd.is_absolute():
        cwd = (root / cwd).resolve()

    if isinstance(cmd_raw, list):
        cmd = [str(x) for x in cmd_raw]
    else:
        cmd = shlex.split(str(cmd_raw), posix=False)

    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=int(check.get("timeout_s", 300)),
        )
        ms = int((time.perf_counter() - t0) * 1000)
        ok = proc.returncode == expected
        tail = (proc.stderr or proc.stdout or "").strip()[-400:]
        detail = f"exit {proc.returncode}" + (f": {tail}" if tail and not ok else "")
        return MvfCheckResult(type="command", ok=ok, detail=detail, duration_ms=ms)
    except subprocess.TimeoutExpired:
        ms = int((time.perf_counter() - t0) * 1000)
        return MvfCheckResult(type="command", ok=False, detail="timeout", duration_ms=ms)
    except OSError as e:
        ms = int((time.perf_counter() - t0) * 1000)
        return MvfCheckResult(type="command", ok=False, detail=str(e), duration_ms=ms)


def run_checks(checks: list[dict[str, Any]], root: Path | None = None) -> MvfResult:
    root = root or app_root()
    results: list[MvfCheckResult] = []

    for check in _normalize_checks(checks):
        ctype = str(check.get("type", "")).strip().lower()
        if ctype == "file_exists":
            rel = str(check.get("path", "")).strip()
            target = _resolve_path(rel, root)
            t0 = time.perf_counter()
            ok = target.is_file()
            ms = int((time.perf_counter() - t0) * 1000)
            results.append(MvfCheckResult(
                type="file_exists",
                ok=ok,
                path=rel,
                detail="ok" if ok else f"missing: {rel}",
                duration_ms=ms,
            ))
        elif ctype == "command":
            results.append(_run_command_check(check, root))
        elif ctype == "dir_exists":
            results.append(_run_dir_exists_check(check, root))
        elif ctype == "dir_count":
            results.append(_run_dir_count_check(check, root))
        else:
            results.append(MvfCheckResult(
                type=ctype or "unknown",
                ok=False,
                detail=f"unsupported check type: {ctype}",
            ))

    validated = bool(results) and all(r.ok for r in results)
    return MvfResult(validated=validated, checks=results)


def validate_session(session_id: str, *, persist: bool = True, root: Path | None = None) -> MvfResult:
    data = load_mvf(session_id)
    if not data:
        return MvfResult(validated=False, checks=[])

    checks = _normalize_checks(data.get("checks"))
    if not checks:
        return MvfResult(validated=False, checks=[])

    result = run_checks(checks, root=root)
    if persist:
        data["validated"] = result.validated
        data["last_run_at"] = _iso_now()
        data["last_results"] = [
            {
                "type": r.type,
                "path": r.path,
                "ok": r.ok,
                "detail": r.detail,
                "duration_ms": r.duration_ms,
            }
            for r in result.checks
        ]
        save_mvf(session_id, data)
    return result


def mvf_enabled(cfg: dict[str, Any] | None) -> bool:
    if not cfg:
        return True
    return bool((cfg.get("mvf") or {}).get("enabled", True))


def mvf_exit_blocked(session_id: str, cfg: dict[str, Any] | None) -> tuple[bool, list[str]]:
    """Return (blocked, failed_details). blocked=True when MVF checks exist and fail."""
    if not mvf_enabled(cfg):
        return False, []
    data = load_mvf(session_id)
    if not data or not data.get("checks"):
        return False, []
    result = validate_session(session_id)
    if result.validated:
        return False, []
    return True, [c.detail for c in result.checks if not c.ok]
