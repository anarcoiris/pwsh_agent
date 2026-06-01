"""Tests for generalized update_from_tool handlers and the anti-narrative guard."""

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.facts_store import (
    contains_narrative,
    load_facts,
    summarize_facts,
    update_from_tool,
)


def _with_tmp(fn):
    with tempfile.TemporaryDirectory() as tmp:
        with patch("core.session_paths.app_root", return_value=Path(tmp)):
            fn()


def test_write_file_records_artifact():
    def body():
        update_from_tool("a1", "write_file", {"success": True}, {"path": "workspace/login_forms.txt"})
        facts = load_facts("a1")
        paths = [a["path"] for a in facts["artifacts"]]
        assert "workspace/login_forms.txt" in paths
        # Idempotent: same path not duplicated.
        update_from_tool("a1", "write_file", {"success": True}, {"path": "workspace/login_forms.txt"})
        assert len(load_facts("a1")["artifacts"]) == 1
    _with_tmp(body)


def test_crack_hash_records_credential():
    def body():
        update_from_tool("a2", "crack_hash", {"success": True, "plaintext": "hunter2"}, {})
        creds = load_facts("a2")["credentials"]
        assert any(c.get("plaintext") == "hunter2" for c in creds)
    _with_tmp(body)


def test_dns_lookup_records_records():
    def body():
        res = {"success": True, "records": [{"value": "93.184.216.34"}, "ns1.example.com"]}
        update_from_tool("a3", "dns_lookup", res, {"hostname": "example.com"})
        dns = load_facts("a3")["dns"]
        assert dns and dns[0]["host"] == "example.com"
        assert "93.184.216.34" in dns[0]["records"]
    _with_tmp(body)


def test_web_tool_records_target():
    def body():
        update_from_tool("a4", "http_headers_check", {"success": True}, {"url": "https://x.test"})
        web = load_facts("a4")["web"]
        assert "https://x.test" in web.get("http_headers_check", [])
    _with_tmp(body)


def test_failed_tool_does_not_write():
    def body():
        assert update_from_tool("a5", "write_file", {"success": False}, {"path": "x"}) is None
    _with_tmp(body)


def test_generalized_facts_summarized():
    def body():
        update_from_tool("a6", "write_file", {"success": True}, {"path": "deliv.txt"})
        update_from_tool("a6", "crack_hash", {"success": True, "plaintext": "pw"}, {})
        text = summarize_facts("a6", max_chars=600)
        assert "artifacts=" in text
        assert "credentials.count" in text
    _with_tmp(body)


def test_contains_narrative_guard():
    clean = {
        "pcap": {"path": "x.pcapng", "credentials_preview": "p" * 500},
        "credentials": [{"source": "crack_hash", "plaintext": "pw"}],
        "artifacts": [{"tool": "write_file", "path": "a.txt"}],
    }
    assert not contains_narrative(clean)

    narrative = {
        "notes": "The analysis revealed that the user logged in. Then we found the hash. "
                 "After that we cracked it successfully."
    }
    assert contains_narrative(narrative)


def test_facts_written_by_tools_have_no_narrative():
    def body():
        update_from_tool("a7", "crack_hash", {"success": True, "plaintext": "pw"}, {})
        update_from_tool("a7", "dns_lookup",
                         {"success": True, "records": ["1.2.3.4"]}, {"hostname": "h"})
        assert not contains_narrative(load_facts("a7"))
    _with_tmp(body)


if __name__ == "__main__":
    test_write_file_records_artifact()
    test_crack_hash_records_credential()
    test_dns_lookup_records_records()
    test_web_tool_records_target()
    test_failed_tool_does_not_write()
    test_generalized_facts_summarized()
    test_contains_narrative_guard()
    test_facts_written_by_tools_have_no_narrative()
    print("ok")
