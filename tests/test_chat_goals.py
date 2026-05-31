"""Tests for chat goals and tool_name JSON parser path."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.chat_goals import ChatGoals, ChatGoalGuard
from core.parser import AgentOutputParser

MSG = "locate last_capture.pcapng and analyze http packets for login, xml, xmlobj using tshark filters"
FOLLOWUP = "can you decode the contents and look for the key values im looking for?"


def test_chat_goals_pcap():
    goals = ChatGoals.from_message(MSG)
    assert goals is not None
    assert "analyze_pcapng" in goals.required_tools
    assert goals.pending([]) == ["analyze_pcapng"]


def test_followup_from_session():
    messages = [
        {"role": "tool", "name": "find_file", "content": json.dumps({"recommended": "last_capture.pcapng"})},
        {"role": "tool", "name": "analyze_pcapng", "content": json.dumps({"success": True, "analysis": {}})},
    ]
    goals = ChatGoals.from_session(messages, FOLLOWUP)
    assert goals is not None
    assert goals.verbose is True
    assert goals.from_session is True


def test_goal_guard_blocks_encode_decode():
    goals = ChatGoals.from_message(FOLLOWUP) or ChatGoals.from_session([], FOLLOWUP)
    goals = goals or ChatGoals(
        required_tools=["analyze_pcapng"],
        pcap_path_hint="last_capture.pcapng",
        label="test",
    )
    _, _, err = ChatGoalGuard.apply(
        "encode_decode",
        {"text": "<user-input>", "operation": "decode"},
        goals,
        [],
    )
    assert err is not None
    assert "analyze_pcapng" in err


def test_goal_guard_blocks_completion_notes():
    goals = ChatGoals(
        required_tools=["analyze_pcapng"],
        pcap_path_hint="last_capture.pcapng",
        label="test",
    )
    _, _, err = ChatGoalGuard.apply(
        "append_note",
        {"path": "workspace/plan.md", "line": "Task completed successfully."},
        goals,
        ["analyze_pcapng"],
    )
    assert err is not None


def test_parser_tool_name_json():
    parser = AgentOutputParser({"append_note": None, "find_file": None})
    content = 'append_note {"path": "workspace/plan.md", "line": "Starting PCAP analysis."}'
    _, _, calls = parser.process_llm_output({"content": content})
    assert calls
    assert calls[0]["function"]["name"] == "append_note"


def test_goal_guard_allows_iterative_analyze_pcap_after_first():
    goals = ChatGoals(
        required_tools=["analyze_pcapng"],
        iterative_tools=["analyze_pcapng", "read_file"],
        label="PCAP analysis",
    )
    _, _, err = ChatGoalGuard.apply(
        "analyze_pcapng",
        {"file_path": "last_capture.pcapng", "filter_expression": 'http contains "login"'},
        goals,
        ["analyze_pcapng"],
    )
    assert err is None


def test_pcap_workflow_not_complete_until_objective_met():
    goals = ChatGoals(
        required_tools=["analyze_pcapng"],
        iterative_tools=["analyze_pcapng", "read_file"],
        label="PCAP analysis",
    )
    assert goals.is_workflow_complete(["analyze_pcapng"], objective_met=False) is False
    assert goals.is_workflow_complete(["analyze_pcapng", "read_file"], objective_met=True) is True


def test_goal_guard_blocks_append_note_when_pcap_pending():
    goals = ChatGoals(
        required_tools=["analyze_pcapng"],
        pcap_path_hint="last_capture.pcapng",
        label="PCAP analysis",
    )
    _, _, err = ChatGoalGuard.apply(
        "append_note",
        {"path": "workspace/status.md", "line": "PCAP file search initiated"},
        goals,
        [],
    )
    assert err is not None
    assert "analyze_pcapng" in err


print("All chat_goals tests passed.")
