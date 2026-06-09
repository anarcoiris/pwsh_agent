"""Shared per-agent tool schema selection for Ollama and context injection."""

from __future__ import annotations

import json
from typing import Any

from core.specialists import SPECIALIST_REGISTRY

# Priority order within each agent (task-critical tools first).
AGENT_TOOL_ORDER: dict[str, tuple[str, ...]] = {
    "lead": (
        "delegate_to",
        "append_note",
        "sequentialthinking",
        "finding_create",
        "finding_list",
        "report_generate",
    ),
    "workspace": (
        "read_file",
        "grep_file",
        "write_file",
        "run_script",
        "host_exec",
        "find_file",
        "find_and_grep",
    ),
    "web": (
        "try_http_login",
        "http_get",
        "http_headers_check",
        "ssl_analysis",
    ),
    "recon": (
        "dns_lookup",
        "ping_sweep",
        "port_scan",
        "system_info",
        "cve_lookup",
    ),
    "forensic": (
        "find_tshark",
        "list_network_interfaces",
        "capture_packets",
        "analyze_pcapng",
    ),
    "crypto": (
        "crack_hash",
        "hash_identify",
        "encode_decode",
    ),
}

# Fits every specialist roster at ~4 chars/token (lead is largest).
DEFAULT_SCHEMA_BUDGET_CHARS = 8000


def tool_names_for_agent(agent_id: str) -> list[str]:
    """Registry tools in priority order (not alphabetical)."""
    reg = SPECIALIST_REGISTRY.get(agent_id, SPECIALIST_REGISTRY["lead"])
    order = AGENT_TOOL_ORDER.get(agent_id)
    if order:
        return [name for name in order if name in reg]
    return sorted(reg)


def schemas_for_agent(
    agent_id: str,
    max_chars: int = DEFAULT_SCHEMA_BUDGET_CHARS,
) -> list[dict[str, Any]]:
    """Return TOOLS_SCHEMA entries for the active specialist.

    Uses priority ordering so task-critical tools (e.g. try_http_login for web)
    are included before lower-priority tools when the char budget is tight.
    """
    import tools

    names = tool_names_for_agent(agent_id)
    out: list[dict[str, Any]] = []
    current_len = 0
    for name in names:
        schema = next(
            (s for s in tools.TOOLS_SCHEMA if s.get("function", {}).get("name") == name),
            None,
        )
        if schema is None:
            continue
        serialized = json.dumps(schema, indent=2)
        if current_len + len(serialized) + 10 > max_chars and out:
            break
        out.append(schema)
        current_len += len(serialized) + 2
    return out


def schemas_json_for_agent(
    agent_id: str,
    max_chars: int = DEFAULT_SCHEMA_BUDGET_CHARS,
) -> str:
    """JSON array string for RELATED TOOL SCHEMAS injection."""
    schemas = schemas_for_agent(agent_id, max_chars=max_chars)
    if not schemas:
        return ""
    return json.dumps(schemas, indent=2)


def missing_registry_schemas() -> list[tuple[str, str]]:
    """Return (agent_id, tool_name) pairs registered but absent from TOOLS_SCHEMA."""
    import tools

    schema_names = {s.get("function", {}).get("name") for s in tools.TOOLS_SCHEMA}
    missing: list[tuple[str, str]] = []
    for agent_id, tool_set in SPECIALIST_REGISTRY.items():
        for tool in tool_set:
            if tool == "delegate_to":
                continue
            if tool not in schema_names:
                missing.append((agent_id, tool))
    return missing
