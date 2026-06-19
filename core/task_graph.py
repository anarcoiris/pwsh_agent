"""Task dependency graph for parallel branch scheduling."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from core.task_plan import StepStatus, TaskStep

if TYPE_CHECKING:
    from core.task_plan import TaskPlanTracker


@dataclass
class TaskGraph:
    """DAG view over TaskPlanTracker steps."""

    steps: list[TaskStep]

    def step_by_id(self) -> dict[str, TaskStep]:
        return {s.id: s for s in self.steps if s.id}

    def ready_steps(self) -> list[TaskStep]:
        """Steps whose dependencies are all DONE and status is PENDING."""
        by_id = self.step_by_id()
        done_ids = {s.id for s in self.steps if s.status == StepStatus.DONE}
        ready: list[TaskStep] = []
        for step in self.steps:
            if step.status != StepStatus.PENDING:
                continue
            deps = [d for d in (step.depends_on or []) if d]
            if all(d in done_ids for d in deps):
                ready.append(step)
        return ready

    def has_cycle(self) -> bool:
        """Detect dependency cycles via DFS."""
        by_id = self.step_by_id()
        visiting: set[str] = set()
        visited: set[str] = set()

        def dfs(sid: str) -> bool:
            if sid in visiting:
                return True
            if sid in visited:
                return False
            visiting.add(sid)
            step = by_id.get(sid)
            if step:
                for dep in step.depends_on or []:
                    if dep in by_id and dfs(dep):
                        return True
            visiting.remove(sid)
            visited.add(sid)
            return False

        return any(dfs(s.id) for s in self.steps if s.id)

    def mark_done(self, step_id: str) -> None:
        for step in self.steps:
            if step.id == step_id:
                step.status = StepStatus.DONE
                return

    @classmethod
    def from_tracker(cls, tracker: "TaskPlanTracker") -> "TaskGraph":
        return cls(steps=list(tracker.steps))
