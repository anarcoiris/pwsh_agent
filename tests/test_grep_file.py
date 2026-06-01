"""Tests for grep_file artifact retrieval helper."""

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tools


def test_grep_file_matches_and_context():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "verbose.txt"
        p.write_text(
            "line1\nPassword,Username,_sessionTOKEN,action\nxmlObj:salt\nline4\n",
            encoding="utf-8",
        )
        res = tools.grep_file(str(p), "xmlObj|Password,Username", max_matches=10, context_lines=1, case_insensitive=True)
        assert res["success"]
        assert res["match_count"] >= 2
        assert any("xmlObj" in m["text"] for m in res["matches"])


def test_grep_file_missing_path():
    res = tools.grep_file("does-not-exist.txt", "x")
    assert not res["success"]


def test_grep_file_glob_verbose_log():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        log_dir = root / ".pulse" / "pcap_logs"
        log_dir.mkdir(parents=True)
        log_file = log_dir / "verbose_20260531_test.txt"
        log_file.write_text("header\nxmlObj:salt value\nfooter\n", encoding="utf-8")
        with patch("core.runtime_paths.search_roots", return_value=(root,)):
            with patch("core.runtime_paths.app_root", return_value=root):
                res = tools.grep_file(".pulse/pcap_logs/verbose_*.txt", "xmlObj")
                assert res["success"], res.get("error")
                assert res["match_count"] >= 1
                assert any("xmlObj" in m["text"] for m in res["matches"])


def test_grep_file_glob_report():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        report_dir = root / "output" / "reports"
        report_dir.mkdir(parents=True)
        report = report_dir / "report_20260531_test.md"
        report.write_text("# Report\nfinding: CVE-2024-1234\n", encoding="utf-8")
        with patch("core.runtime_paths.search_roots", return_value=(root,)):
            with patch("core.runtime_paths.app_root", return_value=root):
                res = tools.grep_file("report_*.md", "CVE-2024")
                assert res["success"], res.get("error")
                assert res["match_count"] >= 1


def test_read_file_blocks_pcap_binary():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "last_capture.pcapng"
        p.write_bytes(b"\x0a\x0d\x0d\x0a\x01\x02binary")
        res = tools.read_file(str(p))
        assert not res["success"]
        assert "analyze_pcapng" in res["error"]


def test_read_file_blocks_artifact_spill():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "state" / "sessions" / "sid" / "artifacts" / "read_file_20260601_035041_x.txt"
        p.parent.mkdir(parents=True)
        p.write_text('{"success": true, "content": "nested"}', encoding="utf-8")
        res = tools.read_file(str(p))
        assert not res["success"]
        assert "artifact" in res["error"].lower()
