"""Tests for roadmap validation (deterministic + cooperative VALIDATE phase)."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.intent_spec import build_fallback_spec
from core.roadmap_validator import deterministic_roadmap_checks, validate_roadmap


GOOD_ROADMAP = [
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
        "depends_on": ["mkdir"],
    },
]

BAD_ROADMAP = [
    {
        "id": "x",
        "label": "",
        "tool_hint": "",
        "assigned_agent": "invalid_agent",
        "success_criteria": "",
    }
]


def test_deterministic_checks_good():
    assert deterministic_roadmap_checks(GOOD_ROADMAP) == []


def test_deterministic_checks_bad_agent():
    issues = deterministic_roadmap_checks(BAD_ROADMAP)
    assert any("assigned_agent" in i for i in issues)


def test_deterministic_checks_unknown_dep():
    steps = [
        {**GOOD_ROADMAP[0], "depends_on": ["missing"]},
    ]
    issues = deterministic_roadmap_checks(steps)
    assert any("depends_on" in i for i in issues)


@pytest.mark.asyncio
async def test_validate_roadmap_disabled():
    spec = build_fallback_spec("Create HelloGame/game.py")
    cfg = {"planner": {"validate_enabled": False}}
    steps, reason = await validate_roadmap(spec, GOOD_ROADMAP, cfg)
    assert steps == GOOD_ROADMAP
    assert reason == ""


@pytest.mark.asyncio
async def test_validate_roadmap_reject_empty():
    spec = build_fallback_spec("test")
    cfg = {
        "planner": {"validate_enabled": True},
        "ollama": {
            "default_model": "qwen2.5-coder:7b-instruct",
            "endpoints": {"coder": "http://localhost:11435"},
            "unload_after_call": False,
        },
    }
    mock_validator = MagicMock()
    mock_validator.validate = AsyncMock(return_value={
        "status": "reject",
        "issues": ["bad step"],
        "reason": "invalid tool_hint",
    })
    with patch("core.roadmap_validator.RoadmapValidator.from_config", return_value=mock_validator):
        steps, reason = await validate_roadmap(spec, BAD_ROADMAP, cfg)
    assert steps == []
    assert "invalid" in reason


@pytest.mark.asyncio
async def test_run_vt_planning_with_validation():
    from agent import ReActAgent

    ag = ReActAgent()
    prompt = "Create HelloGame/game.py ASCII game"
    ag._intent_spec = build_fallback_spec(prompt)
    ag.intent_planner = MagicMock()
    ag.intent_planner.monologue = AsyncMock(return_value="I will scaffold HelloGame.")
    ag.intent_planner.decompose = AsyncMock(return_value=GOOD_ROADMAP)

    with patch("core.roadmap_validator.validate_roadmap", new_callable=AsyncMock) as mock_val:
        mock_val.return_value = (GOOD_ROADMAP, "")
        await ag._run_vt_planning(prompt)

    assert ag._task_plan is not None
    assert ag._task_plan.steps[0].id == "mkdir"
    assert ag._task_plan.steps[1].depends_on == ["mkdir"]
