import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.mission_progress import MissionProgressTracker


def test_tracker_objective_satisfied_with_extracted_secrets():
    tr = MissionProgressTracker(
        "find login/password/xmlObj in pcap and don't stop until retrieved"
    )
    tr.register(
        "analyze_pcapng",
        {"success": True, "analysis": {"extracted_secrets": True}},
        True,
    )
    assert tr.objective_satisfied() is True


def test_tracker_detects_stall_from_notes():
    tr = MissionProgressTracker("retrieve login password")
    tr.register("append_note", {"success": True}, True)
    tr.register("append_note", {"success": True}, True)
    tr.register("find_file", "SKIP: duplicate", False, True)
    assert tr.needs_stall_recovery() is True


def test_tracker_requires_evidence_for_retrieval():
    tr = MissionProgressTracker("retrieve login and password")
    tr.register("analyze_pcapng", {"success": True, "analysis": {}}, True)
    assert tr.objective_satisfied() is False


def test_stall_directive_dev_not_pcap():
    tr = MissionProgressTracker("Propose top 10 must-have .ps1 tools and build them")
    tr.register("append_note", {"success": True}, True)
    tr.register("append_note", {"success": True}, True)
    directive = tr.stall_directive()
    assert "analyze_pcapng" not in directive
    assert "delegate_to" in directive


def test_stall_recovery_on_thinking_spam():
    tr = MissionProgressTracker("write watcher.py script")
    tr.register("sequentialthinking", {"status": "success"}, True)
    tr.register("sequentialthinking", {"status": "success"}, True)
    assert tr.needs_stall_recovery() is True

