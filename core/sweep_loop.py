"""Background sweep — isolated agent for scheduled/orchestrator work."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from core.scheduler import get_due_missions, mark_completed, mark_failed

logger = logging.getLogger("pwsh_agent.core.sweep_loop")

_background_agent: Any | None = None


def get_background_agent() -> Any:
    """Dedicated ReActAgent for sweep/orchestrator — never the interactive console agent."""
    global _background_agent
    if _background_agent is None:
        import agent as agent_mod
        _background_agent = agent_mod.ReActAgent()
        logger.info("Background sweep agent created (session=%s)", _background_agent.session_id)
    return _background_agent


async def sweep_loop(
    interactive_agent: Any | None = None,
    interval_s: int = 60,
    event_callback: Callable | None = None,
    *,
    agent: Any | None = None,
) -> None:
    """Background coroutine — checks scheduler DB and Pulse Queue.

    ``interactive_agent`` (legacy alias ``agent``) is only used for the
    ``_mission_running`` busy flag. Work always runs on ``get_background_agent()``.
    """
    if interactive_agent is None and agent is not None:
        interactive_agent = agent

    bg = get_background_agent()
    logger.info("Sweep loop started (interval=%ds)", interval_s)

    while True:
        await asyncio.sleep(interval_s)
        try:
            try:
                from core.hygiene_missions import poll_hygiene_missions
                polled = poll_hygiene_missions()
                if polled:
                    logger.info("Enqueued %d hygiene mission(s) from feed", polled)
            except Exception as exc:
                logger.debug("Hygiene mission poll skipped: %s", exc)

            busy = bool(getattr(interactive_agent, "_mission_running", False)) if interactive_agent else False
            orch_cfg: dict = {}

            try:
                import yaml
                from core.runtime_paths import app_root
                cfg_path = app_root() / "config.yaml"
                if cfg_path.is_file():
                    with open(cfg_path, encoding="utf-8") as f:
                        orch_cfg = (yaml.safe_load(f) or {}).get("orchestrator") or {}
                if orch_cfg.get("enabled", True):
                    from core.orchestrator import orchestrator_tick
                    result = await orchestrator_tick(bg, interactive_busy=busy)
                    if result.get("ran"):
                        logger.info("Orchestrator ran job %s", result.get("job_id"))
                        continue
            except Exception as exc:
                logger.debug("Orchestrator tick skipped: %s", exc)

            if busy:
                logger.debug("Skipping legacy scheduler — interactive mission in progress")
                continue

            if not orch_cfg.get("run_legacy_scheduler", True):
                continue

            due = get_due_missions()
            if not due:
                continue

            for mission in due:
                mission_id = mission["id"]
                mission_text = mission["mission_text"]
                specialist = mission.get("specialist", "lead")
                network_mode = mission.get("network_mode", "SANDBOX")

                logger.info(
                    "Executing scheduled mission: id=%s specialist=%s",
                    mission_id, specialist,
                )

                saved_spec = getattr(bg, "active_specialist", "lead")
                saved_agent = getattr(bg, "active_agent", "lead")
                saved_mode = getattr(bg, "network_mode", "SANDBOX")

                try:
                    bg.active_agent = specialist
                    bg.active_specialist = specialist
                    bg.network_mode = network_mode

                    if hasattr(bg, "_init_system_prompt"):
                        bg._init_system_prompt()

                    if hasattr(bg, "new_session"):
                        bg.new_session()

                    def _log_event(event_type: str, data: Any) -> None:
                        logger.debug(
                            "Scheduled mission event: %s — %s",
                            event_type, str(data)[:200],
                        )
                        if event_callback:
                            event_callback(event_type, data)

                    await bg.run_mission(mission_text, _log_event)
                    mark_completed(mission_id)
                    logger.info("Scheduled mission completed: %s", mission_id)

                except Exception as exc:
                    err_msg = f"{type(exc).__name__}: {exc}"
                    logger.error(
                        "Scheduled mission failed: id=%s error=%s",
                        mission_id, err_msg,
                    )
                    mark_failed(mission_id, err_msg)

                finally:
                    bg.active_agent = saved_agent
                    bg.active_specialist = saved_spec
                    bg.network_mode = saved_mode
                    if hasattr(bg, "_init_system_prompt"):
                        bg._init_system_prompt()

        except Exception as exc:
            logger.error("Sweep loop error: %s", exc)
