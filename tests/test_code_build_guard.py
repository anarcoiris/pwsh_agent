"""Tests for code_build vs hygiene_remediation mission guards and handoff."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.intent_salvage import mission_lead_dev_guard
from core.task_intent import detect_mission_kind


HELLO_GAME = (
    "Create a HelloGame folder with PLAN.md and a simple Python ASCII game in game.py"
)

HYGIENE_MISSION = (
    "Propose a top 10 must-have .ps1 tools and start building them. Fix REF-001 hygiene finding."
)


def test_detect_code_build_vs_hygiene():
    assert detect_mission_kind(HELLO_GAME) == "code_build"
    assert detect_mission_kind(HYGIENE_MISSION) == "hygiene_remediation"


def test_mission_lead_dev_guard_blocks_hygiene_not_code_build():
    anchor_hygiene = HYGIENE_MISSION
    block_h = mission_lead_dev_guard(
        "write_file",
        [],
        anchor_hygiene,
        active_agent="lead",
    )
    assert block_h is not None
    assert "workspace-owned" in block_h

    block_cb = mission_lead_dev_guard(
        "write_file",
        [],
        HELLO_GAME,
        active_agent="lead",
    )
    assert block_cb is None


def test_lead_may_delegate_code_build():
    block = mission_lead_dev_guard(
        "delegate_to",
        ["sequentialthinking"],
        HELLO_GAME,
        active_agent="lead",
        tool_args={"brief": "Create HelloGame/game.py"},
        last_delegate_brief="",
    )
    assert block is None


def test_handoff_max_tools_default():
    from agent import ReActAgent

    ag = ReActAgent()
    assert ag.handoff_max_tools >= 2
