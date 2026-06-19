"""Regression: http_get artifact-first persistence, facts.web, fetch-before-login gate."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SESSION = "test_http_artifact"


def test_save_http_artifact_full_body():
    import core.session_paths as sp
    import tools.recon as recon

    orig = sp.load_active_session_id
    sp.load_active_session_id = lambda: SESSION  # type: ignore
    try:
        body = "<html>" + ("x" * 150_000) + "xmlobj login</html>"
        path_str = recon._save_http_artifact("http://192.168.1.1/", body)
        assert path_str, "artifact path must be returned"
        p = Path(path_str)
        assert p.is_file()
        text = p.read_text(encoding="utf-8")
        assert len(text) == len(body), "full body persisted, no truncation"
        assert "artifacts" in str(p)
        p.unlink()
    finally:
        sp.load_active_session_id = orig  # type: ignore


def test_facts_store_records_web_page():
    from core.facts_store import load_facts, update_from_tool, summarize_facts
    from core.session_paths import facts_file

    result = {
        "success": True,
        "url": "http://192.168.1.1/",
        "status_code": 200,
        "content_length": 153000,
        "artifact_path": "state/sessions/x/artifacts/http_get_1.html",
        "keyword_hits": ["login", "xmlobj", "password"],
    }
    fp = facts_file(SESSION)
    try:
        update_from_tool(SESSION, "http_get", result, {"url": "http://192.168.1.1/"})
        facts = load_facts(SESSION)
        page = facts["web"]["last_page"]
        assert page["url"] == "http://192.168.1.1/"
        assert page["artifact_path"].endswith("http_get_1.html")
        assert "xmlobj" in page["keyword_hits"]
        assert facts["web"]["pages"][0]["url"] == "http://192.168.1.1/"
        block = summarize_facts(SESSION)
        assert "web.last_page=http://192.168.1.1/" in block
        assert "web.artifact=" in block
        assert "xmlobj" in block
    finally:
        if fp.is_file():
            fp.unlink()


def test_web_specialist_can_grep_artifacts():
    from core.specialists import SPECIALIST_REGISTRY, tool_allowed
    from core.tool_schemas import schemas_for_agent

    assert "grep_file" in SPECIALIST_REGISTRY["web"]
    assert "read_file" in SPECIALIST_REGISTRY["web"]
    assert tool_allowed("web", "grep_file")
    names = [s["function"]["name"] for s in schemas_for_agent("web")]
    assert "grep_file" in names and "try_http_login" in names


def test_fetch_before_login_gate():
    from agent import ReActAgent
    from core.task_plan import StepStatus, TaskPlanTracker, TaskStep

    agent = object.__new__(ReActAgent)
    agent.session_id = SESSION
    agent._task_plan = TaskPlanTracker("login", steps=[
        TaskStep("fetch_page", "GET target URL", "http_get", assigned_agent="web"),
        TaskStep("attempt_login", "Attempt auth", "try_http_login", assigned_agent="web"),
    ])

    err = ReActAgent._fetch_before_login_error(agent)
    assert err and "http_get" in err, "login blocked before fetch"

    agent._task_plan.steps[0].status = StepStatus.DONE
    assert ReActAgent._fetch_before_login_error(agent) is None

    # No plan → no gate
    agent._task_plan = TaskPlanTracker("misc", steps=[])
    assert ReActAgent._fetch_before_login_error(agent) is None


def main() -> int:
    test_save_http_artifact_full_body()
    test_facts_store_records_web_page()
    test_web_specialist_can_grep_artifacts()
    test_fetch_before_login_gate()
    print("OK test_http_get_artifact: artifact spill, facts.web, registry, login gate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
