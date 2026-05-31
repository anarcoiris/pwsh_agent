"""
core/context_router.py — Compose phase hints and RAG injections for LLM turns.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from core.llm_utils import DynamicContextBuilder
from core.query_anchor import resolve_anchor_query, strip_directives
from core.rag import get_rag_context, get_rag_context_for_tools
from core.task_intent import TaskIntent
from core.tool_index import DEFAULT_STATIC_MAX_CHARS, get_static_tool_routing

logger = logging.getLogger("pwsh_agent.core.context_router")

_DEV_TOOLS = ["write_file", "run_script", "read_file", "grep_file", "find_and_grep", "append_note", "host_exec"]
_RECON_TOOLS = ["dns_lookup", "ping_sweep", "port_scan", "system_info"]
_NETWORK_TOOLS = [
    "list_network_interfaces", "capture_packets", "analyze_pcapng", "find_tshark", "find_file", "grep_file", "find_and_grep",
]
_EXPLOIT_TOOLS = ["crack_hash", "hash_identify", "encode_decode"]
_WEB_TOOLS = ["http_headers_check", "ssl_analysis"]
_REPORTING_TOOLS = ["finding_create", "finding_list", "report_generate"]
_INTEL_TOOLS = ["cve_lookup"]

_TOOL_GROUP_MAP: dict[str, frozenset[str]] = {}
for _group in (
    _DEV_TOOLS,
    _RECON_TOOLS,
    _NETWORK_TOOLS,
    _EXPLOIT_TOOLS,
    _WEB_TOOLS,
    _REPORTING_TOOLS,
    _INTEL_TOOLS,
):
    _frozen = frozenset(_group)
    for _tool in _group:
        _TOOL_GROUP_MAP[_tool] = _frozen

_STATIC_ROUTING_HEADER = "### TOOL ROUTING (static reference) ###"
_DOMAIN_MAX_CHARS = 2000
_PLAYBOOK_MAX_CHARS = 2000
_SESSION_CTX_HEADER = "### SESSION CONTEXT ###"
_PLAN_CTX_HEADER = "### TASK PLAN STATUS ###"

_VAGUE_CONTINUE_RE = re.compile(
    r"\b(continue|next step|proceed|go on|keep going|carry on)\b",
    re.I,
)


class ContextRouter:
    """Build transient system messages for OllamaAdapter.chat()."""

    @classmethod
    def build_injections(
        cls,
        messages: list[dict[str, Any]],
        task_intent: TaskIntent | None = None,
        anchor_query: str | None = None,
        session_snippet: str | None = None,
        plan_block: str | None = None,
        injection_budget_chars: int = 8000,
    ) -> list[dict[str, str]]:
        injections: list[dict[str, str]] = []

        query = (anchor_query or "").strip() or resolve_anchor_query(messages)
        query = strip_directives(query)

        if session_snippet:
            injections.append({
                "role": "system",
                "content": f"{_SESSION_CTX_HEADER}\n{session_snippet}\n{'#' * len(_SESSION_CTX_HEADER)}",
            })
        if plan_block:
            injections.append({
                "role": "system",
                "content": f"{_PLAN_CTX_HEADER}\n{plan_block}\n{'#' * len(_PLAN_CTX_HEADER)}",
            })

        static_routing = get_static_tool_routing(max_chars=DEFAULT_STATIC_MAX_CHARS)
        if static_routing:
            injections.append({
                "role": "system",
                "content": (
                    f"{_STATIC_ROUTING_HEADER}\n"
                    f"{static_routing}\n"
                    f"{'#' * len(_STATIC_ROUTING_HEADER)}"
                ),
            })

        phase_hint = DynamicContextBuilder.build_context(messages, anchor_query=query or None)
        phase_label = cls._detect_phase_label(phase_hint, task_intent)

        if phase_hint:
            injections.append({"role": "system", "content": phase_hint})

        domain = get_rag_context(query, max_chars=_DOMAIN_MAX_CHARS) if query else ""
        if domain:
            injections.append({
                "role": "system",
                "content": (
                    "### DOMAIN REFERENCE ###\n"
                    f"{domain}\n"
                    "########################"
                ),
            })

        tool_names = cls._derive_tool_set(messages, task_intent, query, phase_label)
        if tool_names:
            playbooks = get_rag_context_for_tools(tool_names, query, max_chars=_PLAYBOOK_MAX_CHARS)
            if playbooks:
                header = cls._playbook_header(phase_label)
                injections.append({
                    "role": "system",
                    "content": f"{header}\n{playbooks}\n{'#' * len(header)}",
                })

        total = sum(len(i.get("content", "")) for i in injections)
        if total > injection_budget_chars:
            injections = [i for i in injections if "TOOL PLAYBOOKS" not in i.get("content", "")]
            total = sum(len(i.get("content", "")) for i in injections)
        if total > injection_budget_chars:
            injections = [
                i for i in injections
                if "DOMAIN REFERENCE" not in i.get("content", "")
            ]
            total = sum(len(i.get("content", "")) for i in injections)
        if total > injection_budget_chars:
            logger.warning(
                "Injection budget exceeded: %d > %d chars",
                total,
                injection_budget_chars,
            )

        return injections

    @staticmethod
    def _strip_directives(text: str) -> str:
        return strip_directives(text)

    @classmethod
    def _detect_phase_label(cls, phase_hint: str, intent: TaskIntent | None) -> str:
        if intent:
            if intent.mission_kind == "pcap":
                return "NETWORK"
            if intent.mission_kind == "hash":
                return "GENERAL"
            if intent.is_dev_task or intent.mission_kind == "dev":
                return "DEVELOPMENT"
        if "DEVELOPMENT" in phase_hint:
            return "DEVELOPMENT"
        if "PCAP ANALYSIS" in phase_hint:
            return "NETWORK"
        if intent and not intent.is_dev_task:
            lower = phase_hint.lower()
            if "reconnaissance" in lower or "scanning" in lower:
                return "RECON"
            if "enumeration" in lower:
                return "ENUM"
        return "GENERAL"

    @classmethod
    def _recent_successful_tools(
        cls,
        messages: list[dict],
        limit: int = 5,
    ) -> list[str]:
        names: list[str] = []
        for msg in reversed(messages):
            if msg.get("role") != "tool":
                continue
            name = msg.get("name", "")
            if not name:
                continue
            try:
                payload = json.loads(msg.get("content", "{}"))
            except (json.JSONDecodeError, TypeError):
                payload = {}
            if isinstance(payload, dict) and payload.get("success") is False:
                continue
            if name not in names:
                names.append(name)
            if len(names) >= limit:
                break
        return names

    @classmethod
    def _apply_recent_tool_bias(cls, tools: set[str], messages: list[dict]) -> None:
        for name in cls._recent_successful_tools(messages):
            tools.add(name)
            tools.update(_TOOL_GROUP_MAP.get(name, frozenset()))

    @classmethod
    def _derive_tool_set(
        cls,
        messages: list[dict],
        intent: TaskIntent | None,
        query: str,
        phase_label: str,
    ) -> list[str]:
        tools: set[str] = set()
        lower = query.lower()

        if intent and intent.is_dev_task:
            tools.update(_DEV_TOOLS)

        if phase_label == "DEVELOPMENT":
            tools.update(_DEV_TOOLS)
        elif phase_label == "RECON":
            tools.update(_RECON_TOOLS)
        elif phase_label in ("NETWORK", "ENUM"):
            tools.update(_NETWORK_TOOLS)

        if re.search(r"\b(pcap|tshark|wireshark|capture|packet)\b", lower):
            tools.update(_NETWORK_TOOLS)
        if re.search(r"\b(hash|crack|sha-?256|password)\b", lower):
            tools.update(_EXPLOIT_TOOLS)
        if re.search(r"\b(cve|vulnerability|vulnerabilities)\b", lower):
            tools.update(_INTEL_TOOLS)
        if re.search(r"\b(finding|findings|report)\b", lower):
            tools.update(_REPORTING_TOOLS)
        if re.search(r"\b(header|headers|hsts|csp|tls|ssl|certificate|cert)\b", lower):
            tools.update(_WEB_TOOLS)
        if re.search(r"\b(base64|encode|decode|hex|rot13|utf8)\b", lower):
            tools.update(_EXPLOIT_TOOLS)

        if _VAGUE_CONTINUE_RE.search(lower):
            cls._apply_recent_tool_bias(tools, messages)

        for msg in reversed(messages[-12:]):
            if msg.get("role") != "tool":
                continue
            name = msg.get("name", "")
            if name not in ("host_exec", "run_script"):
                continue
            try:
                payload = json.loads(msg.get("content", "{}"))
            except (json.JSONDecodeError, TypeError):
                continue
            failed = (
                payload.get("success") is False
                or payload.get("exit_code", 0) not in (0, None)
            )
            if failed:
                tools.update(["run_script", "host_exec"])
                break

        return sorted(tools)

    @staticmethod
    def _playbook_header(phase_label: str) -> str:
        headers = {
            "DEVELOPMENT": "### TOOL PLAYBOOKS (development) ###",
            "NETWORK": "### TOOL PLAYBOOKS (network capture) ###",
            "RECON": "### TOOL PLAYBOOKS (reconnaissance) ###",
            "ENUM": "### TOOL PLAYBOOKS (enumeration) ###",
        }
        return headers.get(phase_label, "### TOOL PLAYBOOKS ###")
