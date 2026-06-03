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

logger = logging.getLogger("pwsh_agent.core.context_router")

_DEV_TOOLS = ["write_file", "run_script", "read_file", "grep_file", "find_and_grep", "append_note", "host_exec"]
_RECON_TOOLS = ["dns_lookup", "ping_sweep", "port_scan", "system_info"]
_NETWORK_TOOLS = [
    "list_network_interfaces", "capture_packets", "analyze_pcapng", "find_tshark", "find_file", "grep_file", "find_and_grep",
]
_EXPLOIT_TOOLS = ["crack_hash", "hash_identify", "encode_decode"]
_WEB_TOOLS = ["http_get", "http_headers_check", "ssl_analysis", "try_http_login"]
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
        current_state: str | None = None,
        injection_budget_chars: int = 8000,
        *,
        prompt_pack_mode: bool = False,
        active_agent: str = "lead",
    ) -> list[dict[str, str]]:
        injections: list[dict[str, str]] = []

        query = (anchor_query or "").strip() or resolve_anchor_query(messages)
        query = strip_directives(query)

        if current_state:
            # Phase 2: a single canonical block replaces the separate session
            # snippet + plan status injections (already wrapped with its own
            # ### CURRENT STATE ### header by build_current_state()).
            injections.append({"role": "system", "content": current_state})
        else:
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

        if prompt_pack_mode:
            tool_names = cls._schemas_for_active_agent(active_agent)
            if tool_names:
                schemas_json = cls._get_tool_schemas(tool_names, max_chars=4800)
                if schemas_json:
                    injections.append({
                        "role": "system",
                        "content": (
                            "### RELATED TOOL SCHEMAS ###\n"
                            f"{schemas_json}\n"
                            "#############################"
                        ),
                    })
            return injections

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
            
            schemas_json = cls._get_tool_schemas(tool_names)
            if schemas_json:
                injections.append({
                    "role": "system",
                    "content": (
                        "### RELATED TOOL SCHEMAS ###\n"
                        f"{schemas_json}\n"
                        "#############################"
                    ),
                })

        total = sum(len(i.get("content", "")) for i in injections)
        if total > injection_budget_chars:
            injections = [i for i in injections if "TOOL PLAYBOOKS" not in i.get("content", "")]
            total = sum(len(i.get("content", "")) for i in injections)
        if total > injection_budget_chars:
            injections = [i for i in injections if "RELATED TOOL SCHEMAS" not in i.get("content", "")]
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
    def _schemas_for_active_agent(active_agent: str) -> list[str]:
        from core.specialists import SPECIALIST_REGISTRY

        return sorted(SPECIALIST_REGISTRY.get(active_agent, SPECIALIST_REGISTRY["lead"]))

    @staticmethod
    def _strip_directives(text: str) -> str:
        return strip_directives(text)

    @classmethod
    def _get_tool_schemas(cls, tool_names: list[str], max_chars: int = 2400) -> str:
        """Find and format the schemas for the requested tools from TOOLS_SCHEMA."""
        try:
            import tools
            schemas = []
            current_len = 0
            for name in tool_names:
                schema = next((s for s in tools.TOOLS_SCHEMA if s.get("function", {}).get("name") == name), None)
                if schema:
                    serialized = json.dumps(schema, indent=2)
                    if current_len + len(serialized) + 10 > max_chars:
                        if not schemas:
                            # Fallback if a single schema is extremely large
                            return json.dumps(schema)[:max_chars]
                        break
                    schemas.append(schema)
                    current_len += len(serialized) + 2
            if not schemas:
                return ""
            return json.dumps(schemas, indent=2)
        except Exception as e:
            logger.warning("Error getting tool schemas: %s", e)
            return ""

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
        primary_tools: list[str] = []
        secondary_tools: list[str] = []
        lower = query.lower()

        # ── Primary: capability-driven routing (Phase 2) ──────────────────
        # Derive a deterministic IntentSpec from the query and resolve its
        # capabilities → tools. This replaces the old keyword heuristic where a
        # bare "password" surfaced the hash-cracking tools.
        if query:
            try:
                from core.intent_spec import build_fallback_spec
                from core.capabilities import tools_for_capabilities, tools_for_domain
                spec = build_fallback_spec(query)
                primary_tools.extend(tools_for_capabilities(spec.capabilities))
                secondary_tools.extend(tools_for_domain(spec.domain))
            except Exception:
                pass

        if intent and intent.is_dev_task:
            primary_tools.extend(_DEV_TOOLS)

        # ── Secondary: low-priority keyword fallback ──────────────────────
        # Note: "password" is intentionally NOT a trigger for exploit/hash
        # tools — testing a known password is web_auth, not hash cracking.
        if re.search(r"\b(pcap|tshark|wireshark|capture|packet)\b", lower):
            secondary_tools.extend(_NETWORK_TOOLS)
        if re.search(r"\b(hash|crack|sha-?256|sha-?512|md5|digest|hashpro|haspro)\b", lower):
            secondary_tools.extend(_EXPLOIT_TOOLS)
        if re.search(r"\b(log\s?in|login|sign\s?in|authenticate|credential)\b", lower):
            secondary_tools.extend(_WEB_TOOLS)
            primary_tools.append("try_http_login")
        if re.search(r"\b(cve|vulnerability|vulnerabilities)\b", lower):
            secondary_tools.extend(_INTEL_TOOLS)
        if re.search(r"\b(finding|findings|report)\b", lower):
            secondary_tools.extend(_REPORTING_TOOLS)
        if re.search(r"\b(header|headers|hsts|csp|tls|ssl|certificate|cert)\b", lower):
            secondary_tools.extend(_WEB_TOOLS)
        if re.search(r"\b(base64|encode|decode|hex|rot13|utf8)\b", lower):
            secondary_tools.extend(_EXPLOIT_TOOLS)

        if _VAGUE_CONTINUE_RE.search(lower):
            temp_set = set()
            cls._apply_recent_tool_bias(temp_set, messages)
            secondary_tools.extend(temp_set)

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
                primary_tools.extend(["run_script", "host_exec"])
                break

        # De-duplicate while preserving primary priority order
        seen = set()
        ordered_tools = []
        for t in primary_tools + secondary_tools:
            if t not in seen:
                seen.add(t)
                ordered_tools.append(t)
        return ordered_tools

    @staticmethod
    def _playbook_header(phase_label: str) -> str:
        headers = {
            "DEVELOPMENT": "### TOOL PLAYBOOKS (development) ###",
            "NETWORK": "### TOOL PLAYBOOKS (network capture) ###",
            "RECON": "### TOOL PLAYBOOKS (reconnaissance) ###",
            "ENUM": "### TOOL PLAYBOOKS (enumeration) ###",
        }
        return headers.get(phase_label, "### TOOL PLAYBOOKS ###")
