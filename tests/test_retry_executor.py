"""Regression: capped trial-and-error executor (attempts, signatures, BLOCKED)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.task_plan import (
    MAX_STEP_ATTEMPTS,
    StepStatus,
    TaskPlanTracker,
    TaskStep,
    _error_signature,
    _tracker_from_dict,
    _tracker_to_dict,
)


def _tracker() -> TaskPlanTracker:
    t = TaskPlanTracker("test", steps=[
        TaskStep("fix_script", "Fix and run the script", "run_script|host_exec"),
        TaskStep("report", "Report results", "append_note"),
    ])
    return t


def test_error_signature_normalizes_volatile_parts():
    a = _error_signature("InvalidOperation: C:\\x\\s.ps1:14\nLine | 14 | ...")
    b = _error_signature("InvalidOperation: C:\\x\\s.ps1:99\nLine | 99 | ...")
    assert a == b != ""
    assert _error_signature("") == ""
    c = _error_signature("ObjectNotFound: The term 'Ping-IP' is not recognized")
    assert c != a


def test_failure_attempts_count_and_repeat_flag():
    t = _tracker()
    err = {"success": False, "stderr": "InvalidOperation: null-valued expression"}
    info1 = t.register_failure_attempt("run_script", err)
    assert info1 and info1["attempts"] == 1 and not info1["repeat_error"]
    info2 = t.register_failure_attempt("run_script", err)
    assert info2["attempts"] == 2 and info2["repeat_error"]
    # Different error → repeat flag clears
    info3 = t.register_failure_attempt(
        "run_script", {"success": False, "stderr": "ModuleNotFoundError: No module named x"}
    )
    assert info3["attempts"] == 3 and not info3["repeat_error"]


def test_cap_blocks_step_and_advances_roadmap():
    t = _tracker()
    err = {"exit_code": 1, "stderr": "boom"}
    info = None
    for _ in range(MAX_STEP_ATTEMPTS):
        info = t.register_failure_attempt("host_exec", err)
    assert info and info["cap_reached"]
    assert t.steps[0].status == StepStatus.BLOCKED
    # Roadmap advances past the blocked step; no readaptation loop
    assert t.current_step is not None and t.current_step.id == "report"
    assert not t.needs_readaptation()
    # BLOCKED counts as terminal for turn completion
    t.steps[1].status = StepStatus.DONE
    assert t.may_complete_turn(["host_exec"], step_index=5)


def test_meta_tools_and_successes_do_not_count():
    t = _tracker()
    assert t.register_failure_attempt("append_note", {"success": False, "error": "x"}) is None
    assert t.register_failure_attempt("run_script", {"success": True, "exit_code": 0}) is None
    assert t.attempt_counts == {}


def test_success_gating_exit_code():
    t = _tracker()
    # exit_code != 0 must NOT mark the step done even without success=False
    t.register_tool("run_script", {"exit_code": 1, "stderr": "err"}, {})
    assert t.steps[0].status == StepStatus.FAILED
    # exit code 0 completes it
    t.steps[0].status = StepStatus.PENDING
    t.register_tool("run_script", {"exit_code": 0, "stdout": "ok"}, {})
    assert t.steps[0].status == StepStatus.DONE


def test_try_http_login_error_without_verdict_is_not_done():
    t = TaskPlanTracker("login", steps=[
        TaskStep("attempt_login", "Attempt auth", "try_http_login", assigned_agent="web"),
    ])
    t.register_tool("try_http_login", {"success": False, "error": "timeout"}, {})
    assert t.steps[0].status == StepStatus.FAILED
    # A real attempt with verdict (even rejected) completes the step
    t.steps[0].status = StepStatus.PENDING
    t.register_tool("try_http_login", {"success": True, "verdict": "rejected"}, {})
    assert t.steps[0].status == StepStatus.DONE


def test_attempt_state_survives_serialization():
    t = _tracker()
    t.register_failure_attempt("run_script", {"success": False, "stderr": "boom 12"})
    data = _tracker_to_dict(t)
    t2 = _tracker_from_dict(data)
    assert t2.attempt_counts == {"fix_script": 1}
    assert t2.last_error_signatures["fix_script"] == _error_signature("boom 12")


def main() -> int:
    test_error_signature_normalizes_volatile_parts()
    test_failure_attempts_count_and_repeat_flag()
    test_cap_blocks_step_and_advances_roadmap()
    test_meta_tools_and_successes_do_not_count()
    test_success_gating_exit_code()
    test_try_http_login_error_without_verdict_is_not_done()
    test_attempt_state_survives_serialization()
    print("OK test_retry_executor: attempts, signatures, cap/BLOCKED, success gating")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
