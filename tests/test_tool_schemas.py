"""Every specialist registry tool must appear in Ollama schema selection."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.specialists import SPECIALIST_REGISTRY
from core.tool_schemas import (
    DEFAULT_SCHEMA_BUDGET_CHARS,
    missing_registry_schemas,
    schemas_for_agent,
    tool_names_for_agent,
)


def test_no_registry_tools_missing_from_tools_schema():
    assert missing_registry_schemas() == []


def test_web_includes_try_http_login():
    names = [s["function"]["name"] for s in schemas_for_agent("web")]
    assert "try_http_login" in names
    assert "http_get" in names


def test_lead_includes_delegate_to():
    names = [s["function"]["name"] for s in schemas_for_agent("lead")]
    assert "delegate_to" in names
    assert "append_note" in names


def test_all_agent_tools_in_schema_at_default_budget():
    for agent_id, tool_set in SPECIALIST_REGISTRY.items():
        included = {s["function"]["name"] for s in schemas_for_agent(agent_id)}
        for tool in tool_set:
            if tool == "delegate_to":
                continue
            assert tool in included, f"{agent_id}: {tool!r} missing from schemas"


def test_web_priority_puts_login_before_ssl():
    order = tool_names_for_agent("web")
    assert order.index("try_http_login") < order.index("ssl_analysis")


def test_forensic_includes_find_tshark():
    names = [s["function"]["name"] for s in schemas_for_agent("forensic")]
    assert "find_tshark" in names


def test_default_budget_fits_largest_agent():
    lead_schemas = schemas_for_agent("lead", max_chars=DEFAULT_SCHEMA_BUDGET_CHARS)
    assert len(lead_schemas) == len(SPECIALIST_REGISTRY["lead"])
