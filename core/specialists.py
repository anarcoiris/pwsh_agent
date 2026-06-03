"""Specialist registry, tool allowlists, and delegate_to meta-tool."""

from __future__ import annotations

from typing import Any

AGENT_IDS = frozenset({"lead", "workspace", "web", "recon", "forensic", "crypto"})

SPECIALIST_REGISTRY: dict[str, frozenset[str]] = {
    "lead": frozenset({
        "sequentialthinking",
        "delegate_to",
        "append_note",
        "finding_create",
        "finding_list",
        "report_generate",
    }),
    "workspace": frozenset({
        "read_file",
        "write_file",
        "grep_file",
        "find_file",
        "find_and_grep",
        "run_script",
        "host_exec",
    }),
    "web": frozenset({
        "http_get",
        "try_http_login",
        "http_headers_check",
        "ssl_analysis",
    }),
    "recon": frozenset({
        "dns_lookup",
        "ping_sweep",
        "port_scan",
        "system_info",
        "cve_lookup",
    }),
    "forensic": frozenset({
        "list_network_interfaces",
        "capture_packets",
        "analyze_pcapng",
        "find_tshark",
    }),
    "crypto": frozenset({
        "crack_hash",
        "hash_identify",
        "encode_decode",
    }),
}

DOMAIN_SUGGESTED_AGENT: dict[str, str] = {
    "general": "lead",
    "mixed": "lead",
    "reporting": "lead",
    "conversation": "lead",
    "code_build": "workspace",
    "code_review": "workspace",
    "scripting": "workspace",
    "file_ops": "workspace",
    "sysadmin": "workspace",
    "web_auth": "web",
    "recon": "recon",
    "pcap": "forensic",
    "hash": "crypto",
}

AGENT_LABELS: dict[str, str] = {
    "lead": "Orchestrator",
    "workspace": "Files & scripts",
    "web": "HTTP & auth",
    "recon": "Active scanning",
    "forensic": "PCAP pipeline",
    "crypto": "Hash & encoding",
}

# One-line when-to-use summaries for TOOLS.md generation.
TOOL_SUMMARIES: dict[str, str] = {
    "sequentialthinking": "One brief planning thought before acting.",
    "delegate_to": "Hand off to a specialist (LEAD only).",
    "append_note": "Progress line to plan/status/session log.",
    "finding_create": "Record a structured finding.",
    "finding_list": "List findings for this session.",
    "report_generate": "Produce an engagement report from findings.",
    "read_file": "Read file contents from disk.",
    "write_file": "Create or overwrite a deliverable file.",
    "grep_file": "Search a file for a pattern.",
    "find_file": "Locate files by name or glob.",
    "find_and_grep": "Find files then grep matches.",
    "run_script": "Execute a .py or .ps1 script.",
    "host_exec": "Run a PowerShell one-liner (last resort).",
    "http_get": "Fetch a URL body via HTTP GET.",
    "try_http_login": "Test credentials against HTTP Basic or form login.",
    "http_headers_check": "Inspect HTTP response headers.",
    "ssl_analysis": "Analyze TLS certificate and config.",
    "dns_lookup": "Resolve DNS records for a hostname.",
    "ping_sweep": "Discover live hosts in a subnet.",
    "port_scan": "Scan TCP ports on a target.",
    "system_info": "Gather local system information.",
    "cve_lookup": "Look up CVEs by keyword.",
    "list_network_interfaces": "List capture-capable network interfaces.",
    "capture_packets": "Capture packets to a pcapng file.",
    "analyze_pcapng": "Analyze a pcapng for protocols and credentials.",
    "find_tshark": "Locate tshark/Wireshark on the host.",
    "crack_hash": "Crack a hash digest with wordlist.",
    "hash_identify": "Identify hash algorithm by pattern.",
    "encode_decode": "Encode or decode base64/hex/url/rot13.",
}


def all_registry_tools() -> frozenset[str]:
    out: set[str] = set()
    for tools in SPECIALIST_REGISTRY.values():
        out.update(tools)
    return frozenset(out)


def validate_registry() -> list[str]:
    """Return list of validation errors (empty if ok)."""
    errors: list[str] = []
    seen: dict[str, str] = {}
    for agent_id, tool_set in SPECIALIST_REGISTRY.items():
        if agent_id not in AGENT_IDS:
            errors.append(f"unknown agent id: {agent_id}")
        for tool in tool_set:
            if tool in seen:
                errors.append(f"tool {tool!r} in both {seen[tool]!r} and {agent_id!r}")
            else:
                seen[tool] = agent_id
    return errors


def suggested_agent_for_domain(domain: str) -> str:
    return DOMAIN_SUGGESTED_AGENT.get(domain, "lead")


def tool_allowed(active_agent: str, tool_name: str) -> bool:
    allowed = SPECIALIST_REGISTRY.get(active_agent, SPECIALIST_REGISTRY["lead"])
    return tool_name in allowed


def suggest_agent_for_tool(tool_name: str) -> str:
    return _suggest_delegate_for_tool(tool_name)


def scope_advisory_message(tool_name: str, active_agent: str) -> str:
    """Advisory when tool is outside active agent scope (soft allowlist)."""
    suggested = _suggest_delegate_for_tool(tool_name)
    if active_agent == "lead":
        return (
            f"Scope note: {tool_name} is owned by {suggested!r}. "
            f"For cleaner workflow call delegate_to(agent={suggested!r}, brief=...) first; "
            "tool executed anyway."
        )
    return (
        f"Scope note: {tool_name} is not in {active_agent}'s tool set "
        f"(owner: {suggested!r}). Tool executed anyway; "
        "control returns to LEAD after your specialist action."
    )


def allowlist_block_message(tool_name: str, active_agent: str) -> str:
    suggested = _suggest_delegate_for_tool(tool_name)
    if active_agent == "lead":
        return (
            f"Blocked: {tool_name} is not a LEAD tool. "
            f"Call delegate_to(agent={suggested!r}, brief=...) first."
        )
    return (
        f"Blocked: {tool_name} is not available to {active_agent}. "
        "Finish your specialist task; control returns to LEAD automatically."
    )


def _suggest_delegate_for_tool(tool_name: str) -> str:
    for agent_id, tool_set in SPECIALIST_REGISTRY.items():
        if agent_id != "lead" and tool_name in tool_set:
            return agent_id
    return "workspace"


def execute_delegate_to(
    *,
    agent: str,
    brief: str,
    success_criteria: str = "",
) -> dict[str, Any]:
    """Meta-tool: switch active specialist (no side effects beyond state)."""
    agent = (agent or "").strip().lower()
    brief = (brief or "").strip()
    if agent not in AGENT_IDS:
        return {
            "success": False,
            "error": f"Unknown agent {agent!r}. Valid: {', '.join(sorted(AGENT_IDS))}.",
        }
    if agent == "lead":
        return {"success": False, "error": "Use return_to_lead flow; do not delegate_to(lead)."}
    if not brief:
        return {"success": False, "error": "brief is required — describe the specialist task."}
    allowed = sorted(SPECIALIST_REGISTRY.get(agent, frozenset()))
    return {
        "success": True,
        "active_agent": agent,
        "handoff_brief": brief,
        "success_criteria": (success_criteria or "").strip(),
        "allowed_tools": allowed,
    }
