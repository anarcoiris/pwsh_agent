"""Phase 3 tests: web_auth domain planning + try_http_login terminal handling."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.task_plan import TaskPlanTracker, StepStatus


INCIDENT = (
    'try user: user and password: "workspace/pwd.txt" against http://192.168.1.1'
)


def test_web_auth_plan_has_login_step_not_crack():
    plan = TaskPlanTracker(INCIDENT)
    ids = [s.id for s in plan.steps]
    assert "attempt_login" in ids
    assert "crack_hash" not in ids
    assert "extract_secrets" not in ids
    # references the login tool, not analyze_pcapng/crack_hash
    login = next(s for s in plan.steps if s.id == "attempt_login")
    assert login.tool_hint == "try_http_login"


def test_login_attempt_is_terminal_when_rejected():
    plan = TaskPlanTracker(INCIDENT)
    plan.register_tool("read_file", {"success": True, "content": "321123Aa!"})
    plan.register_tool("try_http_login", {"success": True, "authenticated": False,
                                          "verdict": "credentials likely REJECTED"})
    login = next(s for s in plan.steps if s.id == "attempt_login")
    assert login.status == StepStatus.DONE
    assert plan.needs_readaptation() is False


def test_login_attempt_terminal_even_on_network_error():
    plan = TaskPlanTracker(INCIDENT)
    plan.register_tool("try_http_login", {"success": False, "error": "host unreachable"})
    login = next(s for s in plan.steps if s.id == "attempt_login")
    assert login.status == StepStatus.DONE
    assert plan.needs_readaptation() is False


def test_hash_prompt_unaffected_by_web_auth_branch():
    plan = TaskPlanTracker("extract user/password from pcap, save to pwd.txt, use hashpro to crack sha256")
    ids = [s.id for s in plan.steps]
    assert "crack_hash" in ids
    assert "attempt_login" not in ids


print("All web_auth planner tests passed.")
