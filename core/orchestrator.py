"""Unified job orchestrator — idle/night gates + multi-type execution."""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

import yaml

from core.idle_detect import conditions_met, get_idle_time_seconds
from core.runtime_paths import app_root
from core.work_queue import (
    get_runnable_jobs,
    mark_job_completed,
    mark_job_failed,
    mark_job_running,
)

logger = logging.getLogger("pwsh_agent.core.orchestrator")


def _load_orchestrator_config() -> dict[str, Any]:
    cfg_path = app_root() / "config.yaml"
    if not cfg_path.is_file():
        return {}
    with open(cfg_path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("orchestrator") or {}


def pick_next_job(*, allow_urgent: bool = True) -> tuple[dict[str, Any] | None, str]:
    """Select highest-priority runnable job whose idle/night conditions pass."""
    cfg = _load_orchestrator_config()
    default_idle = cfg.get("idle_threshold_seconds", 900)
    night_start = cfg.get("night_start_hour")
    night_end = cfg.get("night_end_hour")
    urgent_min = int(cfg.get("urgent_min_priority", 90))

    for job in get_runnable_jobs():
        idle_req = job.get("requires_idle_seconds")
        if idle_req is None and job.get("night_start_hour") is None:
            idle_req = default_idle if not (night_start is not None) else None

        ns = job.get("night_start_hour") if job.get("night_start_hour") is not None else night_start
        ne = job.get("night_end_hour") if job.get("night_end_hour") is not None else night_end

        ok, reason = conditions_met(
            requires_idle_seconds=idle_req,
            night_start=ns,
            night_end=ne,
            allow_urgent=allow_urgent,
            urgent_min_priority=urgent_min,
            job_priority=int(job.get("priority") or 50),
        )
        if ok:
            return job, reason
    return None, "no eligible jobs"


def _hygiene_root() -> Path | None:
    cfg_path = app_root() / "config.yaml"
    if cfg_path.is_file():
        with open(cfg_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        raw = (data.get("orchestrator") or {}).get("hygiene_root")
        if raw:
            p = Path(raw).expanduser()
            if p.is_dir():
                return p.resolve()
    candidate = app_root().parent / "repo-hygiene"
    if candidate.is_dir():
        return candidate.resolve()
    alt = Path.home() / "Documents" / "repo-hygiene"
    return alt.resolve() if alt.is_dir() else None


async def execute_job(job: dict[str, Any], agent: Any | None = None) -> None:
    """Run one queue job."""
    job_id = job["id"]
    job_type = job["job_type"]
    payload = job.get("payload") or {}
    mark_job_running(job_id)

    try:
        if job_type == "pwsh_mission":
            await _run_pwsh_mission(payload, agent, job)
        elif job_type == "hygiene_review":
            _run_hygiene_review(payload)
        elif job_type == "hygiene_scan":
            _run_hygiene_scan(payload)
        elif job_type == "editorial":
            raise NotImplementedError("editorial jobs — wire Hestia/subagent path")
        elif job_type == "custom":
            _run_custom(payload)
        else:
            raise ValueError(f"Unknown job_type: {job_type}")
        mark_job_completed(job_id)
        logger.info("Job completed: %s (%s)", job_id, job_type)
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"
        mark_job_failed(job_id, err)
        logger.error("Job failed: %s — %s", job_id, err)
        raise


async def _run_pwsh_mission(
    payload: dict[str, Any],
    agent: Any | None,
    job: dict[str, Any] | None = None,
) -> None:
    text = (payload.get("mission_text") or "").strip()
    if not text:
        raise ValueError("empty mission_text")
    if agent is None:
        import agent as agent_mod
        agent = agent_mod.ReActAgent()

    profile = str((job or {}).get("checkpoint_profile") or "mvf_autonomous").strip().lower()
    job_id = (job or {}).get("id")

    checkpoint_cfg = agent.config.setdefault("checkpoint", {})
    saved_profile = checkpoint_cfg.get("profile")
    saved_ask_user = getattr(agent, "ask_user_fn", None)
    checkpoint_cfg["profile"] = profile

    from core.user_checkpoint import CheckpointGate

    async def _silent_ask(_message: str) -> str:
        return ""

    if profile == "mvf_autonomous":
        agent.ask_user_fn = _silent_ask

    specialist = payload.get("specialist", "lead")
    network_mode = payload.get("network_mode", "SANDBOX")
    mvf_override = payload.get("mvf")
    saved_spec = getattr(agent, "active_specialist", "lead")
    saved_mode = getattr(agent, "network_mode", "SANDBOX")
    saved_job_id = getattr(agent, "_active_queue_job_id", None)
    saved_mvf_override = getattr(agent, "_mvf_payload_override", None)

    try:
        agent.active_specialist = specialist
        agent.network_mode = network_mode
        agent._mvf_payload_override = mvf_override
        if hasattr(agent, "_init_system_prompt"):
            agent._init_system_prompt()
        if job_id and hasattr(agent, "begin_queue_job_session"):
            agent.begin_queue_job_session(str(job_id))
            agent._checkpoint_gate = CheckpointGate(agent.session_id, agent.config)
        elif hasattr(agent, "new_session"):
            agent.new_session()
            agent._checkpoint_gate = CheckpointGate(agent.session_id, agent.config)
        else:
            agent._checkpoint_gate = CheckpointGate(agent.session_id, agent.config)
        agent._active_queue_job_id = job_id
        await agent.run_mission(text, None)

        from core.mvf_validator import load_mvf, mvf_enabled, validate_session

        if mvf_enabled(agent.config):
            mvf = load_mvf(agent.session_id)
            if mvf and mvf.get("checks"):
                post = validate_session(agent.session_id)
                if not post.validated:
                    failed = [c.detail for c in post.checks if not c.ok]
                    raise RuntimeError(f"MVF validation failed post-mission: {failed[:3]}")
    finally:
        agent.active_specialist = saved_spec
        agent.network_mode = saved_mode
        agent._active_queue_job_id = saved_job_id
        agent._mvf_payload_override = saved_mvf_override
        agent.ask_user_fn = saved_ask_user
        if saved_profile is not None:
            checkpoint_cfg["profile"] = saved_profile
        elif "profile" in checkpoint_cfg:
            checkpoint_cfg.pop("profile", None)
        if hasattr(agent, "_init_system_prompt"):
            agent._init_system_prompt()
        if hasattr(agent, "_checkpoint_gate"):
            agent._checkpoint_gate = CheckpointGate(agent.session_id, agent.config)


def _run_hygiene_review(payload: dict[str, Any]) -> None:
    root = _hygiene_root()
    if not root:
        raise RuntimeError("repo-hygiene not found; set orchestrator.hygiene_root")
    repo = payload.get("repo_path", "").strip()
    if not repo:
        raise ValueError("repo_path required")
    cmd = [sys.executable, str(root / "scripts" / "ai_reviewer.py"), "--repo", repo]
    target = payload.get("target_file")
    if target:
        cmd.extend(["--file", str(target)])
    _run_subprocess(cmd, cwd=str(root))


def _run_hygiene_scan(payload: dict[str, Any]) -> None:
    root = _hygiene_root()
    if not root:
        raise RuntimeError("repo-hygiene not found")
    repo = payload.get("repo_path", "").strip()
    if not repo:
        raise ValueError("repo_path required")
    cmd = [sys.executable, str(root / "scripts" / "hub_scanner.py"), "--repo", repo]
    _run_subprocess(cmd, cwd=str(root))


def _run_custom(payload: dict[str, Any]) -> None:
    cmd = payload.get("command")
    if not cmd:
        raise ValueError("custom job requires payload.command list")
    cwd = payload.get("cwd")
    _run_subprocess(list(cmd), cwd=cwd)


def _run_subprocess(cmd: list[str], cwd: str | None = None) -> None:
    result = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
    )
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "")[-500:]
        raise RuntimeError(f"exit {result.returncode}: {tail}")


async def orchestrator_tick(
    agent: Any | None = None,
    *,
    force: bool = False,
    interactive_busy: bool = False,
) -> dict[str, Any]:
    """Process at most one queue job. Returns status dict."""
    if interactive_busy and not force:
        return {"ran": False, "reason": "interactive mission in progress"}

    if force:
        jobs = get_runnable_jobs()
        if not jobs:
            return {"ran": False, "reason": "no due jobs"}
        job = jobs[0]
        reason = "forced"
    else:
        job, reason = pick_next_job()
        if not job:
            return {"ran": False, "reason": reason, "idle_s": get_idle_time_seconds()}

    try:
        await execute_job(job, agent)
        return {"ran": True, "job_id": job["id"], "job_type": job["job_type"], "reason": reason}
    except Exception as exc:
        return {"ran": False, "job_id": job["id"], "error": str(exc), "reason": reason}


async def orchestrator_loop(
    agent: Any | None = None,
    interval_s: int = 15,
    stop_event: asyncio.Event | None = None,
    is_busy: Callable[[], bool] | None = None,
) -> None:
    """Background loop for daemon mode."""
    cfg = _load_orchestrator_config()
    interval_s = int(cfg.get("poll_interval_seconds", interval_s))
    logger.info("Orchestrator loop started (interval=%ds)", interval_s)

    while True:
        if stop_event and stop_event.is_set():
            break
        busy = is_busy() if is_busy else False
        try:
            await orchestrator_tick(agent, interactive_busy=busy)
        except Exception as exc:
            logger.error("Orchestrator tick error: %s", exc)
        await asyncio.sleep(interval_s)
