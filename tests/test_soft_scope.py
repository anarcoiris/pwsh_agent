"""Soft specialist scope advisory tests."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.specialists import scope_advisory_message, suggest_agent_for_tool, tool_allowed


def test_lead_may_run_web_tool_with_advisory_message():
    assert not tool_allowed("lead", "try_http_login")
    msg = scope_advisory_message("try_http_login", "lead")
    assert "web" in msg
    assert "delegate_to" in msg
    assert "executed anyway" in msg


def test_suggest_agent_for_tool():
    assert suggest_agent_for_tool("http_get") == "web"
    assert suggest_agent_for_tool("analyze_pcapng") == "forensic"
