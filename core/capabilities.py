"""
core/capabilities.py — Capability registry (Phase 2).

Maps capability tags (see core/intent_spec.CAPABILITIES) to the concrete tools
that satisfy them. This decouples *what needs to happen* (capability) from
*which tool does it*, so routing/planning can reason about capabilities and new
domains become registrations instead of edits to keyword regexes.

Used by core/context_router to resolve IntentSpec.capabilities → tool names,
replacing the old keyword heuristic where the bare word "password" surfaced the
hash-cracking tools.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Capability:
    name: str
    tools: tuple[str, ...]
    domains: tuple[str, ...] = ()
    summary: str = ""
    when_to_use: str = ""
    network_egress: bool = False
    destructive: bool = False
    system_modification: bool = False


# Canonical capability → tool mapping. Tool names match the agent's registry
# (see tools/__init__.py and the system prompt's AVAILABLE TOOLS list).
_CAPABILITIES: dict[str, Capability] = {}


def _register(cap: Capability) -> None:
    _CAPABILITIES[cap.name] = cap


for _cap in (
    Capability("file_read", ("read_file", "grep_file", "find_file", "find_and_grep"),
               ("file_ops", "code_review", "pcap"),
               "Read/search files on disk.", "Inspecting file contents or locating files."),
    Capability("file_write", ("write_file",),
               ("file_ops", "code_build", "scripting", "reporting"),
               "Create or overwrite a file.", "Producing a file deliverable."),
    Capability("file_edit", ("read_file", "write_file"),
               ("file_ops",),
               "Revise an existing file.", "Editing/refactoring an existing file."),
    Capability("http_auth_attempt", ("try_http_login",),
               ("web_auth",),
               "Try credentials against an HTTP endpoint (Basic + common form login).",
               "User wants to test/verify a username+password against a web service.",
               network_egress=True),
    Capability("http_inspect", ("http_get", "http_headers_check", "ssl_analysis"),
               ("web_auth", "recon"),
               "Fetch a URL's body and/or inspect HTTP response headers / security posture.",
               "Fetching a web page or examining a web endpoint's headers or TLS.",
               network_egress=True),
    Capability("http_fetch", ("http_get",),
               ("web_auth", "recon"),
               "Retrieve the body (HTML/text) of a URL via HTTP GET.",
               "User wants to GET/fetch/download/retrieve a web page or its HTML.",
               network_egress=True),
    Capability("port_scan", ("port_scan",), ("recon",),
               "Scan TCP ports on a target.", "Enumerating open ports.",
               network_egress=True),
    Capability("dns_lookup", ("dns_lookup",), ("recon",),
               "Resolve DNS records.", "Resolving hostnames/records.",
               network_egress=True),
    Capability("ping_sweep", ("ping_sweep",), ("recon",),
               "Discover live hosts in a subnet.", "Host discovery.",
               network_egress=True),
    Capability("ssl_inspect", ("ssl_analysis",), ("recon", "web_auth"),
               "Analyze TLS certificate/config.", "Examining TLS posture.",
               network_egress=True),
    Capability("pcap_analyze", ("analyze_pcapng", "capture_packets", "list_network_interfaces",
                                "find_file", "grep_file", "find_and_grep"),
               ("pcap",),
               "Capture/analyze packet traces.", "Working with pcap/pcapng data."),
    Capability("hash_crack", ("crack_hash",), ("hash",),
               "Brute-force/crack a hash digest.",
               "Cracking an actual hash digest — NOT a known plaintext."),
    Capability("hash_identify", ("hash_identify",), ("hash",),
               "Identify a hash algorithm by pattern.", "Classifying a hash string."),
    Capability("encode_decode", ("encode_decode",), ("hash",),
               "Encode/decode text (base64/hex/url/rot13).", "Transforming encoded data."),
    Capability("code_review", ("read_file", "grep_file", "find_and_grep"),
               ("code_review",),
               "Review source for issues.", "Auditing/reviewing code."),
    Capability("static_scan", ("grep_file", "find_and_grep"), ("code_review",),
               "Pattern-scan source for markers.", "Searching code for risky patterns."),
    Capability("code_build", ("write_file", "run_script", "host_exec"),
               ("code_build",),
               "Author and run code.", "Building/implementing code."),
    Capability("scaffold", ("write_file",), ("code_build",),
               "Create project/file scaffolding.", "Generating boilerplate files."),
    Capability("scripting", ("write_file", "run_script", "host_exec"),
               ("scripting",),
               "Author PowerShell/Python scripts.", "Writing/running scripts.",
               system_modification=False),
    Capability("task_schedule", ("host_exec",), ("sysadmin",),
               "Manage scheduled tasks/services via PowerShell.",
               "Creating/modifying scheduled tasks or services.",
               system_modification=True),
    Capability("system_info", ("system_info",), ("sysadmin",),
               "Gather local system information.", "Inspecting the local machine."),
    Capability("cve_lookup", ("cve_lookup",), ("recon", "reporting"),
               "Look up CVEs by keyword.", "Researching vulnerabilities.",
               network_egress=True),
    Capability("reporting", ("finding_create", "finding_list", "report_generate"),
               ("reporting",),
               "Record findings and generate reports.", "Documenting/reporting results."),
    Capability("conversation", (), ("conversation",),
               "No tool — direct conversational answer.", "Answering a question directly."),
):
    _register(_cap)


def get_capability(name: str) -> Capability | None:
    return _CAPABILITIES.get(name)


def tools_for_capabilities(capabilities: list[str] | tuple[str, ...]) -> list[str]:
    """Resolve a list of capability tags to a de-duplicated, ordered tool list."""
    out: list[str] = []
    for cap_name in capabilities or ():
        cap = _CAPABILITIES.get(cap_name)
        if not cap:
            continue
        for tool in cap.tools:
            if tool not in out:
                out.append(tool)
    return out


def tools_for_domain(domain: str) -> list[str]:
    """All tools whose capabilities list the given domain."""
    out: list[str] = []
    for cap in _CAPABILITIES.values():
        if domain in cap.domains:
            for tool in cap.tools:
                if tool not in out:
                    out.append(tool)
    return out


def capabilities_for_tool(tool_name: str) -> list[str]:
    """Reverse lookup: which capabilities a tool participates in."""
    return [c.name for c in _CAPABILITIES.values() if tool_name in c.tools]


def all_capability_names() -> list[str]:
    return list(_CAPABILITIES.keys())
