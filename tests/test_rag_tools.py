"""Tests for tool-indexed RAG retrieval and ContextRouter hardening."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.context_router import ContextRouter
from core.rag import LocalRAG, reload_rag
from core.tool_index import (
    DEFAULT_STATIC_MAX_CHARS,
    get_static_tool_routing,
    reload_static_routing_cache,
)

_TOOLS_DIR = Path(__file__).resolve().parent.parent / "knowledge" / "tools"
_STATIC_HEADER = "### TOOL ROUTING (static reference) ###"


def test_loads_frontmatter_tools():
    rag = LocalRAG()
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


def test_rag_results_include_anchor_reference():
    rag = LocalRAG()
    result = rag.retrieve_for_tools(["analyze_pcapng"], "pcap login xmlobj", max_chars=2000)
    assert "#" in result


def test_recursive_tools_directory():
    rag = LocalRAG()
    files = {s["file"] for s in rag.sections}
    assert any(f.startswith("tools/") for f in files)


def test_static_routing_loads_and_respects_cap():
    reload_static_routing_cache()
    content = get_static_tool_routing(max_chars=500)
    assert content
    assert len(content) <= 500
    assert "write_file" in content or "Quick Routing" in content


def test_static_routing_default_cap():
    reload_static_routing_cache()
    content = get_static_tool_routing(max_chars=DEFAULT_STATIC_MAX_CHARS)
    assert content
    assert len(content) <= DEFAULT_STATIC_MAX_CHARS


def test_build_injections_excludes_static_routing():
    messages = [{"role": "user", "content": "port scan 192.168.1.1"}]
    injections = ContextRouter.build_injections(messages)
    combined = "\n".join(i.get("content", "") for i in injections)
    assert "TOOL ROUTING (static" not in combined


def test_build_injections_includes_schemas_for_matched_tools():
    messages = [{"role": "user", "content": "scan the network"}]
    injections = ContextRouter.build_injections(messages)
    schemas = [i for i in injections if "### RELATED TOOL SCHEMAS ###" in i.get("content", "")]
    assert len(schemas) == 1
    assert "ping_sweep" in schemas[0]["content"] or "port_scan" in schemas[0]["content"]


def test_derive_tool_set_reporting_keywords():
    tools = ContextRouter._derive_tool_set(
        messages=[{"role": "user", "content": "generate the engagement report"}],
        intent=None,
        query="generate the engagement report",
        phase_label="GENERAL",
    )
    assert "report_generate" in tools
    assert "finding_list" in tools


def test_derive_tool_set_web_keywords():
    tools = ContextRouter._derive_tool_set(
        messages=[{"role": "user", "content": "check TLS certificate on example.com"}],
        intent=None,
        query="check TLS certificate on example.com",
        phase_label="GENERAL",
    )
    assert "ssl_analysis" in tools
    assert "http_headers_check" in tools


def test_derive_tool_set_cve_keywords():
    tools = ContextRouter._derive_tool_set(
        messages=[{"role": "user", "content": "lookup CVE for OpenSSL"}],
        intent=None,
        query="lookup CVE for OpenSSL",
        phase_label="GENERAL",
    )
    assert "cve_lookup" in tools


def test_derive_tool_set_recent_tool_bias_on_continue():
    messages = [
        {"role": "user", "content": "analyze pcap"},
        {
            "role": "tool",
            "name": "analyze_pcapng",
            "content": '{"success": true, "analysis": {}}',
        },
        {"role": "user", "content": "continue"},
    ]
    tools = ContextRouter._derive_tool_set(
        messages=messages,
        intent=None,
        query="continue",
        phase_label="GENERAL",
    )
    assert "analyze_pcapng" in tools
    assert "capture_packets" in tools or "find_file" in tools


def test_routing_sections_present_in_core_playbooks():
    required = [
        "write_file.md",
        "run_script.md",
        "append_note.md",
        "host_exec.md",
        "analyze_pcapng.md",
        "crack_hash.md",
        "find_tshark.md",
        "dns_lookup.md",
    ]
    for name in required:
        text = (_TOOLS_DIR / name).read_text(encoding="utf-8")
        assert "## When to Use" in text or "## Routing" in text, f"{name} missing When to Use/Routing"
        assert "Do Not Use" in text, f"{name} missing Do Not Use section"


def test_injection_budgets():
    messages = [{"role": "user", "content": "port scan 192.168.1.1"}]
    injections = ContextRouter.build_injections(messages)
    for inj in injections:
        assert len(inj.get("content", "")) <= 2500


if __name__ == "__main__":
    test_loads_frontmatter_tools()
    test_retrieve_for_tools_capture_packets()
    test_retrieve_for_tools_run_script()
    test_recursive_tools_directory()
    test_static_routing_loads_and_respects_cap()
    test_static_routing_default_cap()
    test_build_injections_includes_schemas_for_matched_tools()
    test_derive_tool_set_reporting_keywords()
    test_derive_tool_set_web_keywords()
    test_derive_tool_set_cve_keywords()
    test_derive_tool_set_recent_tool_bias_on_continue()
    test_routing_sections_present_in_core_playbooks()
    test_injection_budgets()
    reload_rag()
    print("All rag_tools tests passed.")
