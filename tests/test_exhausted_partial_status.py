"""Component 1 — exhausted crack_hash maps to PARTIAL (bounded terminal), not FAILED."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.task_plan import TaskPlanTracker, StepStatus


def _crack_plan() -> TaskPlanTracker:
    return TaskPlanTracker(
        "extract user/password/xmlObj from pcap, save to pwd.txt, use hashpro to crack sha256"
    )


def test_exhausted_maps_to_partial():
    plan = _crack_plan()
    plan.register_tool("crack_hash", {"success": True, "status": "exhausted"})
    crack = next(s for s in plan.steps if s.id == "crack_hash")
    assert crack.status == StepStatus.PARTIAL
    assert "exhausted" in crack.note.lower()


def test_exhausted_does_not_trigger_readaptation():
    plan = _crack_plan()
    plan.register_tool("crack_hash", {"success": True, "status": "exhausted"})
    assert plan.needs_readaptation() is False


def test_genuine_failure_still_marks_failed():
    plan = _crack_plan()
    plan.register_tool("crack_hash", {"success": False, "error": "launcher missing"})
    crack = next(s for s in plan.steps if s.id == "crack_hash")
    assert crack.status == StepStatus.FAILED
    assert plan.needs_readaptation() is True


def test_may_complete_turn_with_partial_and_done():
    plan = _crack_plan()
    for s in plan.steps:
        if s.id != "crack_hash":
            s.status = StepStatus.DONE
    plan.register_tool("crack_hash", {"success": True, "status": "exhausted"})
    assert plan.may_complete_turn(["crack_hash"], step_index=2) is True
    assert plan.may_complete_turn(["crack_hash"], step_index=1) is False


def test_write_file_not_blocked_after_exhausted():
    plan = _crack_plan()
    plan.register_tool("crack_hash", {"success": True, "status": "exhausted"})
    plan.register_tool(
        "write_file",
        {"success": True},
        {"path": "pwd.txt", "content": "user=alice\npassword=NOT CRACKED (exhausted)\nxmlObj=abc123"},
    )
    assert plan.needs_readaptation() is False
    write_step = next(s for s in plan.steps if s.id == "write_deliverable")
    assert write_step.status in (StepStatus.DONE, StepStatus.PARTIAL)
