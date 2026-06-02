"""report_generate session scope — must not dump stale cross-session findings."""

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.session_paths import session_start_iso


def test_session_start_iso_parses():
    assert session_start_iso("20260602_214652") == "2026-06-02T21:46:52+00:00"
    assert session_start_iso("bad") is None


def test_report_generate_session_scope_excludes_old_findings():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db = root / "state" / "findings.db"
        db.parent.mkdir(parents=True, exist_ok=True)

        with patch("tools.intel._DB_PATH", db), patch("tools.intel.app_root", return_value=root):
            from tools.intel import finding_create, report_generate, _get_db

            _get_db()
            finding_create(
                title="Stale finding",
                severity="CRITICAL",
                description="From an old engagement",
                session_id="20260101_000000",
            )
            finding_create(
                title="Current session finding",
                severity="INFO",
                description="Router HTML review",
                session_id="20260602_214652",
            )

            res = report_generate(
                title="Router analysis",
                scope="session",
                session_id="20260602_214652",
            )
            assert res["success"] is True, res
            report_file = Path(res["report_path"])
            if not report_file.is_file():
                report_file = root / "output" / report_file.name
            text = report_file.read_text(encoding="utf-8")
            assert "Current session finding" in text
            assert "Stale finding" not in text

            empty = report_generate(scope="session", session_id="20990101_000000")
            assert empty["success"] is False
            assert "task_summary" in empty["error"] or "finding_create" in empty["error"]
