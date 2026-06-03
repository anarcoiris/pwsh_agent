"""Tests for delegate_to meta-tool and specialist allowlist."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.specialists import (
    execute_delegate_to,
    scope_advisory_message,
    tool_allowed,
)


def test_execute_delegate_to_success():
    result = execute_delegate_to(
        agent="web",
        brief="GET router login page",
        success_criteria="HTML retrieved",
    )
    assert result["success"] is True
    assert result["active_agent"] == "web"
    assert "http_get" in result["allowed_tools"]


def test_execute_delegate_to_rejects_lead():
    result = execute_delegate_to(agent="lead", brief="noop")
    assert result["success"] is False


def test_execute_delegate_to_rejects_unknown():
    result = execute_delegate_to(agent="network", brief="old persona")
    assert result["success"] is False


def test_web_cannot_delegate():
    assert not tool_allowed("web", "delegate_to")


def test_lead_cannot_http_get():
    assert not tool_allowed("lead", "http_get")


def test_lead_block_message_mentions_delegate():
    msg = scope_advisory_message("http_get", "lead")
    assert "delegate_to" in msg
    assert "web" in msg


def test_specialist_advisory_message():
    msg = scope_advisory_message("append_note", "web")
    assert "lead" in msg or "web" in msg
