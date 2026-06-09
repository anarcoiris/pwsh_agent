"""Orphan specialist handoff reset tests."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import ReActAgent


def test_reset_handoff_to_lead_from_web():
    agent = ReActAgent.__new__(ReActAgent)
    agent.prompt_pack_mode = True
    agent.active_agent = "web"
    agent.active_specialist = "web"
    agent._handoff_brief = "fetch page"
    agent._return_to_lead_when = ""
    agent._handoff_complete = False
    agent._refresh_system_prompt = lambda: None

    changed = agent.reset_handoff_to_lead(reason="test")
    assert changed is True
    assert agent.active_agent == "lead"
    assert agent.active_specialist == "lead"
    assert agent._handoff_brief == ""


def test_reset_orphan_skips_when_lead():
    agent = ReActAgent.__new__(ReActAgent)
    agent.prompt_pack_mode = True
    agent.active_agent = "lead"
    agent.active_specialist = "lead"
    agent._handoff_brief = ""
    agent._handoff_complete = False
    agent.reset_handoff_to_lead = lambda **k: False  # type: ignore[method-assign]

    agent._reset_orphan_specialist(when="test")
    assert agent.active_agent == "lead"
