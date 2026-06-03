"""Manager task plan with assigned agents."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.task_plan import TaskPlanTracker


def test_web_auth_plan_assigns_agents():
    plan = TaskPlanTracker("login to http://192.168.1.1 with user and password")
    assert len(plan.steps) >= 2
    agents = {s.assigned_agent for s in plan.steps}
    assert "web" in agents


def test_manager_plan_in_compact():
    plan = TaskPlanTracker("login to http://192.168.1.1")
    compact = plan.compact()
    assert compact.get("manager_plan")
    assert any("agent=web" in line for line in compact["manager_plan"])
    assert compact.get("current_task", {}).get("assigned_agent") == "web"


def test_current_state_includes_manager_plan():
    from core.working_state import build_current_state

    block = build_current_state(
        mission="login test",
        manager_plan=["task=t1 agent=web status=pending label=GET /"],
        current_task={"id": "t1", "assigned_agent": "web", "label": "GET /"},
    )
    assert "[MANAGER PLAN]" in block
    assert "[CURRENT TASK]" in block
    assert "agent=web" in block
