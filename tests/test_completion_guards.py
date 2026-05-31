"""Tests for mission/chat completion guards."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.chat_goals import ChatGoals
from core.mission_progress import MissionProgressTracker


def test_generic_mission_requires_substantive_tools():
    tracker = MissionProgressTracker("scan the network and report findings")
    assert tracker.objective_satisfied() is False
    tracker.register("dns_lookup", {"success": True}, True)
    assert tracker.objective_satisfied() is False
    tracker.register("port_scan", {"success": True, "open_ports": []}, True)
    assert tracker.objective_satisfied() is True


def test_retrieval_mission_still_requires_evidence():
    tracker = MissionProgressTracker("retrieve login password from pcap")
    tracker.register("analyze_pcapng", {"success": True, "analysis": {}}, True)
    assert tracker.objective_satisfied() is False
    tracker.register("read_file", {"success": True, "content": "password=secret"}, True)
    assert tracker.objective_satisfied() is True


def test_chat_may_end_turn_requires_min_steps():
    goals = ChatGoals(required_tools=["port_scan"], label="Port scan")
    executed = [{"name": "port_scan", "success": True}]
    assert goals.is_workflow_complete(executed) is True
    assert goals.may_end_turn(executed, step=0) is False
    assert goals.may_end_turn(executed, step=2) is True


def test_pcap_may_end_turn_requires_objective_and_depth():
    goals = ChatGoals(
        required_tools=["analyze_pcapng"],
        label="PCAP analysis",
        hints={"pcap_path_hint": "last_capture.pcapng"},
        iterative_tools=["analyze_pcapng", "read_file"],
    )
    executed = [{"name": "analyze_pcapng", "success": True}]
    assert goals.may_end_turn(executed, step=3, objective_met=True) is False
    executed2 = [
        {"name": "analyze_pcapng", "success": True},
        {"name": "read_file", "success": True},
    ]
    assert goals.may_end_turn(executed2, step=3, objective_met=True) is True
    assert goals.may_end_turn(executed2, step=1, objective_met=True) is False


if __name__ == "__main__":
    test_generic_mission_requires_substantive_tools()
    test_retrieval_mission_still_requires_evidence()
    test_chat_may_end_turn_requires_min_steps()
    test_pcap_may_end_turn_requires_objective_and_depth()
    print("All completion guard tests passed.")
