"""Tests for session facts persistence and summaries."""

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.facts_store import load_facts, summarize_facts, update_from_tool


def test_update_from_analyze_pcapng_writes_facts():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        with patch("core.session_paths.app_root", return_value=root):
            res = {
                "success": True,
                "analysis": {
                    "key_fields": "Password,Username,_sessionTOKEN,action",
                    "potential_plaintext_credentials": "xmlObj:salt",
                    "verbose_log_file": ".pulse/pcap_logs/verbose_x.txt",
                },
            }
            path = update_from_tool("sid1", "analyze_pcapng", res, {"file_path": "last_capture.pcapng"})
            assert path is not None and path.exists()
            facts = load_facts("sid1")
            assert facts["pcap"]["path"] == "last_capture.pcapng"
            assert "xmlobj" in facts["pcap"]["keywords"]
            assert "xmlObj" in facts["pcap"]["credentials_preview"] or "Password" in facts["pcap"]["http_forms_preview"]


def test_summarize_facts_compact():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        with patch("core.session_paths.app_root", return_value=root):
            update_from_tool(
                "sid2",
                "ping_sweep",
                {"success": True, "live_hosts": [{"IP": "192.168.1.10"}]},
                {},
            )
            text = summarize_facts("sid2", max_chars=500)
            assert text.startswith("[SESSION FACTS]")
            assert "192.168.1.10" in text
