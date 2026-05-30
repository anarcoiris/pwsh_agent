"""Tests for artifact path resolution and find_file."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.artifacts import resolve_project_file, find_file
import tools

MSG = "locate a file named last_capture.pcapng"


def test_resolve_last_capture():
    p = resolve_project_file("last_capture.pcapng")
    assert p is not None and p.exists(), "last_capture.pcapng should resolve"


def test_network_logs_not_found():
    p = resolve_project_file("network_logs/last_capture.pcapng")
    assert p is None


def test_find_file_tool():
    res = tools.find_file("last_capture.pcapng")
    assert res["success"]
    assert res["recommended"]
    assert "last_capture.pcapng" in res["recommended"]


def test_analyze_pcapng_resolves_basename():
    res = tools.analyze_pcapng("last_capture.pcapng", limit=2)
    assert res.get("success"), res.get("error", res)


print("All artifacts tests passed.")
