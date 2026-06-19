"""Tests for task graph parallel scheduling."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.task_graph import TaskGraph
from core.task_plan import StepStatus, TaskPlanTracker, TaskStep
from core.task_scheduler import run_ready_steps


def _tracker_with_parallel_steps() -> TaskPlanTracker:
    t = TaskPlanTracker.__new__(TaskPlanTracker)
    t.prompt = "test"
    t.strategy_notes = []
    t.last_failure = ""
    t.evidence_seen = set()
    t.attempt_counts = {}
    t.last_error_signatures = {}
    t.steps = [
        TaskStep(id="a", label="Step A", tool_hint="write_file", parallel_group="g1"),
        TaskStep(id="b", label="Step B", tool_hint="grep_file", parallel_group="g1"),
        TaskStep(id="c", label="Step C", tool_hint="read_file", depends_on=["a", "b"]),
    ]
    return t


def test_ready_steps_initial_batch():
    graph = TaskGraph.from_tracker(_tracker_with_parallel_steps())
    ready = graph.ready_steps()
    assert {s.id for s in ready} == {"a", "b"}


def test_ready_steps_after_first_done():
    tracker = _tracker_with_parallel_steps()
    tracker.steps[0].status = StepStatus.DONE
    graph = TaskGraph.from_tracker(tracker)
    ready = graph.ready_steps()
    assert {s.id for s in ready} == {"b"}


def test_cycle_detection():
    tracker = TaskPlanTracker.__new__(TaskPlanTracker)
    tracker.steps = [
        TaskStep(id="x", label="X", tool_hint="t", depends_on=["y"]),
        TaskStep(id="y", label="Y", tool_hint="t", depends_on=["x"]),
    ]
    assert TaskGraph.from_tracker(tracker).has_cycle() is True


def test_parallel_run_ready_steps():
    tracker = _tracker_with_parallel_steps()
    ran: list[str] = []

    async def _run(step: TaskStep) -> None:
        await asyncio.sleep(0.01)
        ran.append(step.id)
        step.status = StepStatus.DONE

    attempted = asyncio.run(run_ready_steps(tracker, _run, max_parallel=2))
    assert len(attempted) == 2
    assert set(ran) == {"a", "b"}
    graph = TaskGraph.from_tracker(tracker)
    assert {s.id for s in graph.ready_steps()} == {"c"}
