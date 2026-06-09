"""Specialist handoff must not complete on LEAD-only tools."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.specialists import specialist_hard_block, tool_allowed


def test_append_note_hard_blocked_for_web():
    assert specialist_hard_block("web", "append_note")
    assert not tool_allowed("web", "append_note")


def test_append_note_allowed_for_lead():
    assert not specialist_hard_block("lead", "append_note")
    assert tool_allowed("lead", "append_note")


def test_http_get_in_scope_for_web():
    assert tool_allowed("web", "http_get")
    assert not specialist_hard_block("web", "http_get")


def test_specialist_action_nudge_includes_tool_and_url():
    from core.specialists import specialist_action_nudge

    msg = specialist_action_nudge(
        "web",
        "Fetch the HTML content",
        "http_get",
        url="http://192.168.1.1/",
    )
    assert "http_get" in msg
    assert "192.168.1.1" in msg
    assert "append_note" in msg
    assert "forbidden" in msg


def test_extract_target_url():
    from core.specialists import extract_target_url

    assert extract_target_url("", ["http://192.168.1.1/"]) == "http://192.168.1.1/"
    assert extract_target_url("login to http://10.0.0.1/admin", []) == "http://10.0.0.1/admin"
