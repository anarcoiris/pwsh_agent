"""Tests for task plan tracking and placeholder pwd.txt detection."""

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.task_plan import (
    TaskPlanTracker,
    _looks_like_placeholder_file,
    save_plan_state,
    load_plan_state,
    clear_plan_state,
)
from core.task_plan import StepStatus


def test_plan_state_persist_and_reload():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        with patch("core.session_paths.app_root", return_value=root):
            sid = "plan_persist_sid"
            plan = TaskPlanTracker("extract pwd.txt and crack hash")
            plan.steps[0].status = StepStatus.DONE
            save_plan_state(sid, plan)
            loaded = load_plan_state(sid)
            assert loaded is not None
            assert loaded.steps[0].status == StepStatus.DONE
            assert len(loaded.steps) == len(plan.steps)
            for s in loaded.steps:
                s.status = StepStatus.DONE
            save_plan_state(sid, loaded)
            assert load_plan_state(sid) is None
            clear_plan_state(sid)


def test_load_plan_state_discards_on_domain_mismatch():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        with patch("core.session_paths.app_root", return_value=root):
            sid = "domain_mismatch_sid"
            # Persist a hash-domain roadmap with an in-progress step.
            plan = TaskPlanTracker("crack this sha256 hash with hashpro")
            save_plan_state(sid, plan)
            assert load_plan_state(sid) is not None  # no message -> kept
            # New, unrelated message (web_auth) must discard + clear the stale plan.
            assert load_plan_state(sid, "login to http://192.168.1.1 as user with pwd.txt") is None
            assert load_plan_state(sid) is None  # cleared from disk


def test_load_plan_state_kept_on_same_domain():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        with patch("core.session_paths.app_root", return_value=root):
            sid = "domain_same_sid"
            plan = TaskPlanTracker("crack this sha256 hash with hashpro")
            save_plan_state(sid, plan)
            assert load_plan_state(sid, "now crack the sha256 hash again") is not None


def test_load_plan_state_kept_when_new_message_generic():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        with patch("core.session_paths.app_root", return_value=root):
            sid = "domain_generic_sid"
            plan = TaskPlanTracker("crack this sha256 hash with hashpro")
            save_plan_state(sid, plan)
            # A generic/conversational follow-up should not nuke an active plan.
            assert load_plan_state(sid, "thanks, what is the status?") is not None


def test_non_pcap_password_prompt_has_no_extract_secrets():
    plan = TaskPlanTracker("review the password handling in auth.py for issues")
    ids = [s.id for s in plan.steps]
    assert "extract_secrets" not in ids


def test_find_file_wildcard_error_is_neutral():
    from core.artifacts import find_file

    with tempfile.TemporaryDirectory() as tmp:
        res = find_file("zzz_no_match_*.qqq", search_root=Path(tmp))
    assert res["success"] is False
    err = res["error"].lower()
    for biased in ("pcap", "verbose_", "xmlobj", "analyze_pcapng"):
        assert biased not in err


def test_parse_extract_and_crack_steps():
    prompt = (
        "read latest reports, extract user/password/xmlObj from pcap, "
        "save to pwd.txt, use hashpro to crack sha256"
    )
    plan = TaskPlanTracker(prompt)
    ids = [s.id for s in plan.steps]
    assert "read_context" in ids
    assert "extract_secrets" in ids
    assert "write_deliverable" in ids
    assert "crack_hash" in ids


def test_placeholder_write_marks_failed():
    plan = TaskPlanTracker("save values to pwd.txt and crack hash")
    plan.register_tool(
        "write_file",
        {"success": True},
        {"path": "workspace/pwd.txt", "content": "user:password\nxmlObj:salt"},
    )
    assert plan.needs_readaptation()
    write_step = next(s for s in plan.steps if s.id == "write_deliverable")
    assert write_step.status.value == "failed"


def test_may_not_complete_with_failed_step():
    plan = TaskPlanTracker("pwd.txt and crack_hash")
    plan.register_tool(
        "write_file",
        {"success": True},
        {"path": "pwd.txt", "content": "user:password"},
    )
    assert not plan.may_complete_turn(["write_file"], step_index=5)


def test_empty_pwd_marks_failed():
    plan = TaskPlanTracker("save to pwd.txt")
    plan.register_tool(
        "write_file",
        {"success": True},
        {"path": "pwd.txt", "content": ""},
    )
    assert plan.needs_readaptation()


def test_placeholder_detection():
    assert _looks_like_placeholder_file("user:password\nxmlObj:salt")
    assert not _looks_like_placeholder_file("user=alice\npassword=hunter2\nxmlObj=abc123")
    assert _looks_like_placeholder_file("xmlObj: 1234567890abcdef\nsalt: abcdef1234567890")
    assert _looks_like_placeholder_file("# HTTP login forms (from analyze_pcapng http_forms)\n")


def test_read_file_resets_failed_extract():
    plan = TaskPlanTracker("extract user/password from last_capture.pcapng and write pwd.txt")
    for s in plan.steps:
        if s.id == "extract_secrets":
            s.status = plan.steps[0].status.__class__.FAILED
    plan.register_tool("read_file", {"success": True}, {"path": "output/report_1.md"})
    extract = next(s for s in plan.steps if s.id == "extract_secrets")
    assert extract.status.value == "failed"


def test_read_file_with_credential_evidence_resets_failed_extract():
    plan = TaskPlanTracker("extract user/password from last_capture.pcapng and write pwd.txt")
    for s in plan.steps:
        if s.id == "extract_secrets":
            s.status = plan.steps[0].status.__class__.FAILED
    plan.register_tool(
        "read_file",
        {"success": True, "content": "Password,Username,_sessionTOKEN,action xmlObj=salt"},
        {"path": "workspace/pcap_logs/verbose.txt"},
    )
    extract = next(s for s in plan.steps if s.id == "extract_secrets")
    assert extract.status.value in ("pending", "done")
