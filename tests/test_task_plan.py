"""Tests for task plan tracking and placeholder pwd.txt detection."""

from core.task_plan import TaskPlanTracker, _looks_like_placeholder_file


def test_parse_extract_and_crack_steps():
    prompt = (
        "read latest reports, extract user/password/xmlObj from pcap, "
        "save to pwd.txt, use hashpro to crack sha256"
    )
    plan = TaskPlanTracker(prompt)
    ids = [s.id for s in plan.steps]
    assert "read_context" in ids
    assert "extract_secrets" in ids
    assert "write_pwd" in ids
    assert "crack_hash" in ids


def test_placeholder_write_marks_failed():
    plan = TaskPlanTracker("save values to pwd.txt and crack hash")
    plan.register_tool(
        "write_file",
        {"success": True},
        {"path": "workspace/pwd.txt", "content": "user:password\nxmlObj:salt"},
    )
    assert plan.needs_readaptation()
    write_step = next(s for s in plan.steps if s.id == "write_pwd")
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


def test_read_file_resets_failed_extract():
    plan = TaskPlanTracker("extract and write pwd.txt")
    for s in plan.steps:
        if s.id == "extract_secrets":
            s.status = plan.steps[0].status.__class__.FAILED
    plan.register_tool("read_file", {"success": True}, {"path": "output/report_1.md"})
    extract = next(s for s in plan.steps if s.id == "extract_secrets")
    assert extract.status.value in ("pending", "done")
