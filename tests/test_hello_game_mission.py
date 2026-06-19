"""HelloGame mission bootstrap uses IntentSpec paths, not toolN.ps1."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.intent_spec import IntentSpec, build_fallback_spec
from core.task_intent import detect_mission_kind


PROMPT = (
    "In HelloGame/ create PLAN.md and game.py — a minimal Python ASCII game."
)


@pytest.mark.asyncio
async def test_code_build_bootstrap_delegate_brief():
    from agent import ReActAgent

    ag = ReActAgent()
    ag._intent_spec = build_fallback_spec(PROMPT)
    ag._anchor_query = PROMPT
    assert detect_mission_kind(PROMPT) == "code_build"

    brief = ag._code_build_delegate_brief(PROMPT)
    assert "tool1.ps1" not in brief.lower()
    assert "HelloGame" in brief or "game.py" in brief or "Deliverables" in brief

    ag._execute_tool = AsyncMock(return_value=(True, 1))
    with patch.object(ag, "_refresh_system_prompt"):
        executed = await ag._bootstrap_code_build_mission(PROMPT, set())
    assert executed == ["delegate_to"]
    args = ag._execute_tool.await_args[0]
    assert args[0] == "delegate_to"
    assert "tool1.ps1" not in str(args[1]).lower()


@pytest.mark.asyncio
async def test_workspace_fallback_writes_deliverable_not_ps1():
    from agent import ReActAgent

    ag = ReActAgent()
    ag._anchor_query = PROMPT
    ag._intent_spec = build_fallback_spec(PROMPT)
    ag.active_agent = "workspace"
    ag._mission_tools_executed = []

    ag._execute_tool = AsyncMock(return_value=(True, 1))
    executed = await ag._bootstrap_specialist_action(set())
    assert "write_file" in executed
    path_arg = ag._execute_tool.await_args[0][1]["path"]
    assert "tool1.ps1" not in path_arg
    assert "game.py" in path_arg or "HelloGame" in path_arg
