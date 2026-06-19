"""Parallel execution scheduler for independent roadmap branches."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, TYPE_CHECKING

from core.task_graph import TaskGraph
from core.task_plan import StepStatus, TaskStep

if TYPE_CHECKING:
    from core.task_plan import TaskPlanTracker

logger = logging.getLogger("pwsh_agent.core.task_scheduler")

StepRunner = Callable[[TaskStep], Awaitable[Any]]


async def run_ready_steps(
    tracker: "TaskPlanTracker",
    run_step: StepRunner,
    *,
    max_parallel: int = 2,
) -> list[TaskStep]:
    """Execute all currently-ready steps with bounded parallelism.

    Returns the list of steps that were attempted this round.
    """
    graph = TaskGraph.from_tracker(tracker)
    if graph.has_cycle():
        logger.warning("Task graph has dependency cycle — running sequentially")
        max_parallel = 1

    ready = graph.ready_steps()
    if not ready:
        return []

    batch = ready[: max(1, max_parallel)]
    attempted: list[TaskStep] = []

    async def _run_one(step: TaskStep) -> None:
        try:
            await run_step(step)
            if step.status not in (StepStatus.DONE, StepStatus.FAILED, StepStatus.BLOCKED):
                step.status = StepStatus.DONE
        except Exception as exc:
            step.status = StepStatus.FAILED
            step.note = str(exc)[:300]
            logger.warning("Parallel step %s failed: %s", step.id, exc)

    if len(batch) == 1:
        await _run_one(batch[0])
        attempted.append(batch[0])
    else:
        await asyncio.gather(*(_run_one(s) for s in batch))
        attempted.extend(batch)

    return attempted


def parallel_branches_enabled(cfg: dict[str, Any]) -> bool:
    return bool(cfg.get("planner", {}).get("parallel_branches", False))


def max_parallel_branches(cfg: dict[str, Any]) -> int:
    return max(1, int(cfg.get("planner", {}).get("max_parallel_branches", 2)))
