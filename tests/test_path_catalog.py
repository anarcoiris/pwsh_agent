"""Tests for unified artifact path catalog."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.path_catalog import resolve_read_target, LEGACY_SESSION_NOTE_PATHS
from core.task_intent import TaskIntentExtractor
from core.task_plan import load_session_context_snippets
from core.runtime_paths import app_root


def test_resolve_read_target_glob_reports():
    resolved, err = resolve_read_target("report_*.md")
    if resolved:
        assert resolved.is_file()
        assert err is None
    else:
        assert err


def test_legacy_plan_not_in_session_snippet():
    snippet = load_session_context_snippets(app_root(), Path.cwd(), session_id="20260531_221133")
    if snippet:
        assert "workspace/plan.md" not in snippet.lower() or "not legacy" in snippet.lower()
    assert "workspace/plan.md" in LEGACY_SESSION_NOTE_PATHS


def test_login_forms_named_deliverable():
    msg = "save in a file named 'login_forms.txt' then crack"
    intent = TaskIntentExtractor.parse(msg)
    assert any("login_forms.txt" in d for d in intent.deliverables)


def test_resolve_write_target_bare_deliverable():
    from core.path_catalog import resolve_write_target
    from core.session_paths import session_workspace_dir

    sid = "test_write_sid"
    target = resolve_write_target("pwd.txt", sid, deliverables=["pwd.txt"])
    assert target.parent == session_workspace_dir(sid).resolve()
    assert target.name == "pwd.txt"


def test_write_file_routes_bare_deliverable_to_session_workspace():
    import tempfile
    from unittest.mock import patch

    import tools
    from core.session_paths import session_workspace_dir

    sid = "test_write_route"
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        with patch("core.runtime_paths.app_root", return_value=root):
            with patch("core.session_paths.load_active_session_id", return_value=sid):
                res = tools.write_file("pwd.txt", "user=admin\npass=secret", session_id=sid)
                assert res["success"], res.get("error")
                expected = session_workspace_dir(sid) / "pwd.txt"
                assert expected.is_file()
                assert "admin" in expected.read_text(encoding="utf-8")
