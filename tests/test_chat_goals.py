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


def test_pending_requires_success_events():
    goals = ChatGoals(required_tools=["read_file", "analyze_pcapng"], label="X")
    executed = [
        {"name": "read_file", "success": True},
        {"name": "analyze_pcapng", "success": False},
    ]
    assert goals.pending(executed) == ["analyze_pcapng"]


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
        blocked_tools=["encode_decode"],
        blocked_reason="Do NOT use encode_decode on PCAP data.",
    )
    _, _, err = ChatGoalGuard.apply(
        "encode_decode",
        {"text": "<user-input>", "operation": "decode"},
        goals,
        [],
    )
    assert err is not None
    assert "encode_decode" in err.lower() or "pcap" in err.lower()


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


def test_strategy_note_bypass_when_pending():
    goals = ChatGoals(
        required_tools=["write_file", "crack_hash"],
        label="Extract credentials and crack hash",
    )
    _, _, err = ChatGoalGuard.apply(
        "append_note",
        {"path": "workspace/plan.md", "line": "Strategy change: use verbose log"},
        goals,
        [{"name": "read_file", "success": True}],
        strategy_note=True,
    )
    assert err is None


def test_goal_guard_allows_one_sequentialthinking_then_blocks():
    goals = ChatGoals(
        required_tools=["crack_hash"],
        label="Hash cracking",
    )
    _, _, err1 = ChatGoalGuard.apply(
        "sequentialthinking",
        {"thought": "Plan"},
        goals,
        [],
    )
    assert err1 is None
    _, _, err2 = ChatGoalGuard.apply(
        "sequentialthinking",
        {"thought": "Plan again"},
        goals,
        [{"name": "sequentialthinking", "success": True}],
    )
    assert err2 is not None


def test_actionable_extract_prompt_not_misclassified_as_display_only():
    msg = (
        "Extract a list with the Password hashes and salts, use hashpro, "
        "then give me the results and write pwd_0106.txt"
    )
    goals = ChatGoals.from_message(msg)
    assert goals is not None
    assert goals.label != "Display session facts"
    assert "crack_hash" in goals.required_tools


def test_display_only_facts_prompt_uses_read_file_goal():
    msg = "Show me the facts. Show me the Password hash and the salt."
    goals = ChatGoals.from_message(msg)
    assert goals is not None
    assert goals.label == "Display session facts"
    assert goals.required_tools == ["read_file"]


def test_extract_pcap_hashes_to_custom_pwd_file_uses_multistep_goal():
    msg = (
        "Find last_capture.pcapng. Extract a list with Password hashes and salt, "
        "use hashpro, and write results to pwd_0106.txt"
    )
    goals = ChatGoals.from_message(msg)
    assert goals is not None
    assert goals.label == "Extract credentials and crack hash"
    assert goals.required_tools == ["read_file", "analyze_pcapng", "crack_hash", "write_file"]
    assert goals.hints.get("deliverable_path") == "pwd_0106.txt"


def test_pending_keeps_crack_hash_when_hash_not_from_pcap():
    """Wrong/placeholder crack_hash must not clear crack_hash from pending."""
    goals = ChatGoals(
        required_tools=["read_file", "analyze_pcapng", "crack_hash", "write_file"],
        label="Extract credentials and crack hash",
        hints={"deliverable_path": "pwd.txt"},
    )
    executed = [
        {"name": "read_file", "success": True},
        {"name": "analyze_pcapng", "success": True},
        {
            "name": "crack_hash",
            "success": False,
            "args": {
                "target_hash": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            },
        },
    ]
    assert "crack_hash" in goals.pending(executed)
    assert "write_file" in goals.pending(executed)


def test_goal_guard_blocks_append_note_before_crack():
    goals = ChatGoals(
        required_tools=["read_file", "analyze_pcapng", "crack_hash", "write_file"],
        label="Extract credentials and crack hash",
        hints={"deliverable_path": "pwd.txt", "mask": "NNNNNNAA!"},
    )
    executed = [
        {"name": "read_file", "success": True},
        {"name": "analyze_pcapng", "success": True},
    ]
    _, _, err = ChatGoalGuard.apply(
        "append_note",
        {"path": "workspace/plan.md", "line": "still working"},
        goals,
        executed,
    )
    assert err is not None
    assert "crack_hash" in err


def test_goal_guard_blocks_write_before_crack():
    goals = ChatGoals(
        required_tools=["read_file", "analyze_pcapng", "crack_hash", "write_file"],
        label="Extract credentials and crack hash",
        hints={"deliverable_path": "pwd_1234.txt"},
    )
    executed = [
        {"name": "read_file", "success": True},
        {"name": "analyze_pcapng", "success": True},
    ]
    _, _, err = ChatGoalGuard.apply(
        "write_file",
        {"path": "pwd_1234.txt", "content": "hash=abc"},
        goals,
        executed,
    )
    assert err is not None
    assert "crack_hash" in err


def test_goal_guard_allows_write_after_terminal_crack_events():
    """write_file must not stay blocked when crack_hash exhausted/cracked in events."""
    goals = ChatGoals(
        required_tools=["read_file", "analyze_pcapng", "crack_hash", "write_file"],
        label="Extract credentials and crack hash",
        hints={"deliverable_path": "pwd.txt"},
    )
    executed = [
        {"name": "read_file", "success": True},
        {"name": "analyze_pcapng", "success": True},
        {"name": "crack_hash", "success": True},
        {"name": "crack_hash", "success": True},  # exhausted attempts also count
        {"name": "crack_hash", "success": True},
    ]
    _, _, err = ChatGoalGuard.apply(
        "write_file",
        {
            "path": "pwd.txt",
            "content": "hash=934a4dba\npassword=321123Aa!\n",
        },
        goals,
        executed,
    )
    assert err is None


print("All chat_goals tests passed.")
