"""Tests for tool-indexed RAG retrieval."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.rag import LocalRAG


def test_loads_frontmatter_tools():
    rag = LocalRAG()
    tool_sets = {tuple(sorted(s.get("tools", []))) for s in rag.sections}
    assert any("capture_packets" in s.get("tools", []) for s in rag.sections)


def test_retrieve_for_tools_capture_packets():
    rag = LocalRAG()
    result = rag.retrieve_for_tools(["capture_packets"], "pcap analysis", max_chars=3000)
    assert result
    assert "capture_packets" in result.lower() or "analyze_pcapng" in result.lower()


def test_retrieve_for_tools_run_script():
    rag = LocalRAG()
    result = rag.retrieve_for_tools(["run_script"], "python watcher", max_chars=3000)
    assert result
    assert "run_script" in result.lower() or ".py" in result.lower()


def test_recursive_tools_directory():
    rag = LocalRAG()
    files = {s["file"] for s in rag.sections}
    assert any(f.startswith("tools/") for f in files)


print("All rag_tools tests passed.")
