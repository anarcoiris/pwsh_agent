"""report_generate session scope — must not dump stale cross-session findings."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tools.intel as intel


def test_report_generate_session_scope_excludes_old_findings(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "findings.db"
        monkeypatch.setattr(intel, "_DB_PATH", db)

        fc1 = intel.finding_create(
            title="Stale",
            severity="CRITICAL",
            description="old engagement",
            session_id="20260601_000000",
        )
        fc2 = intel.finding_create(
            title="Current",
            severity="INFO",
            description="this session",
            session_id="20260602_214652",
        )
        assert fc1["success"] and fc2["success"]

        report = intel.report_generate(scope="session", session_id="20260602_214652")
        assert report["success"] is True, report
        text = Path(report["report_path"]).read_text(encoding="utf-8")
        assert "Current" in text
        assert "Stale" not in text
        assert report["findings_count"] == 1


def test_report_generate_session_empty_fails_without_dumping_all(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "findings.db"
        monkeypatch.setattr(intel, "_DB_PATH", db)
        intel.finding_create(
            title="Stale only",
            severity="CRITICAL",
            description="old",
            session_id="20260601_000000",
        )
        fail = intel.report_generate(scope="session", session_id="20260602_214652")
        assert fail["success"] is False
        assert "finding_create" in fail["error"] or "this session" in fail["error"].lower()
