"""Tests for specialist registry integrity."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tools
from core.specialists import (
    AGENT_IDS,
    SPECIALIST_REGISTRY,
    all_registry_tools,
    suggested_agent_for_domain,
    tool_allowed,
    validate_registry,
)


def test_no_tool_in_two_agents():
    assert validate_registry() == []


def test_all_registry_tools_in_agent_registry():
    """Every specialist tool (except delegate_to) must exist in tools registry."""
    reg = {"sequentialthinking"}
    for name in tools.__all__:
        if name not in ("SequentialThinkingEngine", "TOOLS_SCHEMA", "sequentialthinking"):
            reg.add(name)
    for tool in all_registry_tools():
        if tool == "delegate_to":
            continue
        assert tool in reg, f"{tool!r} missing from tools registry"


def test_domain_suggestions():
    assert suggested_agent_for_domain("web_auth") == "web"
    assert suggested_agent_for_domain("pcap") == "forensic"
    assert suggested_agent_for_domain("hash") == "crypto"
    assert suggested_agent_for_domain("unknown") == "lead"


def test_lead_cannot_use_http_get():
    assert not tool_allowed("lead", "http_get")
    assert tool_allowed("web", "http_get")


def test_six_agents():
    assert len(AGENT_IDS) == 6
    assert set(SPECIALIST_REGISTRY.keys()) == AGENT_IDS
