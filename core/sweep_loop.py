"""Background sweep loop that checks for due scheduled missions.

Inspired by NanoClaw v2's ``host-sweep.ts`` — runs inside the same
asyncio event loop as the REPL and checks the scheduler DB every
*interval_s* seconds.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from core.scheduler import get_due_missions, mark_completed, mark_failed

logger = logging.getLogger("pwsh_agent.core.sweep_loop")


async def sweep_loop(
    agent: Any,
    interval_s: int = 60,
    event_callback: Callable | None = None,
) -> None:
    """Background coroutine — checks scheduler DB for due missions.

    Parameters
    ----------
    agent : ReActAgent
        The live agent instance whose ``run_mission`` will be called.
    interval_s : int
        Poll interval in seconds (default 60).
    event_callback : callable, optional
        Forwarded to ``agent.run_mission()`` for UI events.
    """
    logger.info("Sweep loop started (interval=%ds)", interval_s)

    while True:
        await asyncio.sleep(interval_s)
        try:
            # Poll hygiene-feed mission stubs (repo-hygiene eyes)
            try:
                from core.hygiene_missions import poll_hygiene_missions
                polled = poll_hygiene_missions()
                if polled:
                    logger.info("Enqueued %d hygiene mission(s) from feed", polled)
            except Exception as exc:
                logger.debug("Hygiene mission poll skipped: %s", exc)

            due = get_due_missions()
            if not due:
                continue

            if getattr(agent, "_mission_running", False):
                logger.debug("Skipping scheduled missions — interactive mission in progress")
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

                # Save and restore agent state around the scheduled execution
                saved_specialist = getattr(agent, "active_specialist", "lead")
                saved_mode = getattr(agent, "network_mode", "SANDBOX")
                saved_session = getattr(agent, "session_id", None)

                try:
                    # Configure agent for this mission
                    agent.active_specialist = specialist
                    agent.network_mode = network_mode

                    # Refresh prompt with new specialist/mode context
                    if hasattr(agent, "_init_system_prompt"):
                        agent._init_system_prompt()

                    # Create a fresh session for the scheduled run
                    if hasattr(agent, "new_session"):
                        agent.new_session()

                    def _log_event(event_type: str, data: Any) -> None:
                        logger.debug(
                            "Scheduled mission event: %s — %s",
                            event_type, str(data)[:200],
                        )
                        if event_callback:
                            event_callback(event_type, data)

                    await agent.run_mission(mission_text, _log_event)
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
                    # Restore original agent state
                    agent.active_specialist = saved_specialist
                    agent.network_mode = saved_mode
                    if saved_session and hasattr(agent, "session_id"):
                        agent.session_id = saved_session
                    if hasattr(agent, "_init_system_prompt"):
                        agent._init_system_prompt()

        except Exception as exc:
            logger.error("Sweep loop error: %s", exc)
