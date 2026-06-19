"""IntentPlanner roadmap feeds TaskPlanTracker.from_vt_roadmap."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.intent_spec import build_fallback_spec
from core.task_plan import TaskPlanTracker, StepStatus


ROADMAP = [
    {
        "id": "mkdir",
        "label": "Create HelloGame directory",
        "tool_hint": "write_file",
        "assigned_agent": "workspace",
        "success_criteria": "HelloGame/ exists",
    },
    {
        "id": "game",
        "label": "Write game.py",
        "tool_hint": "write_file",
        "assigned_agent": "workspace",
        "success_criteria": "HelloGame/game.py exists",
    },
]

PROMPT = "Create HelloGame/game.py ASCII game"


def test_from_vt_roadmap_steps():
    plan = TaskPlanTracker.from_vt_roadmap(PROMPT, ROADMAP)
    assert len(plan.steps) == 2
    assert plan.steps[0].id == "mkdir"
    assert plan.steps[1].assigned_agent == "workspace"


@pytest.mark.asyncio
async def test_run_vt_planning_replaces_regex_plan():
    from agent import ReActAgent

    ag = ReActAgent()
    ag._intent_spec = build_fallback_spec(PROMPT)
    ag.intent_planner = MagicMock()
    ag.intent_planner.monologue = AsyncMock(return_value="I will scaffold HelloGame first.")
    ag.intent_planner.decompose = AsyncMock(return_value=ROADMAP)

    await ag._run_vt_planning(PROMPT)
    assert ag._task_plan is not None
    assert ag._task_plan.steps[0].id == "mkdir"
    assert ag._vt_monologue.startswith("I will")
