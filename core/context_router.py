"""
core/context_router.py — Compose phase hints and RAG injections for LLM turns.
"""

from __future__ import annotations

import json
import re
from typing import Any

from core.llm_utils import DynamicContextBuilder
from core.rag import get_rag_context, get_rag_context_for_tools
from core.task_intent import TaskIntent

_DEV_TOOLS = ["write_file", "run_script", "read_file", "append_note", "host_exec"]
_RECON_TOOLS = ["dns_lookup", "ping_sweep", "port_scan", "system_info"]
_NETWORK_TOOLS = [
    "list_network_interfaces", "capture_packets", "analyze_pcapng", "find_tshark", "find_file",
]
_EXPLOIT_TOOLS = ["crack_hash", "hash_identify", "encode_decode"]


class ContextRouter:
    """Build transient system messages for OllamaAdapter.chat()."""

    @classmethod
    def build_injections(
        cls,
        messages: list[dict[str, Any]],
        task_intent: TaskIntent | None = None,
    ) -> list[dict[str, str]]:
        injections: list[dict[str, str]] = []

        phase_hint = DynamicContextBuilder.build_context(messages)
        phase_label = cls._detect_phase_label(phase_hint, task_intent)

        if phase_hint:
            injections.append({"role": "system", "content": phase_hint})

        user_msgs = [m for m in messages if m.get("role") == "user"]
        latest = user_msgs[-1].get("content", "") if user_msgs else ""
        query = cls._strip_directives(latest)

        domain = get_rag_context(query, max_chars=2000)
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
            playbooks = get_rag_context_for_tools(tool_names, query, max_chars=2000)
            if playbooks:
                header = cls._playbook_header(phase_label)
                injections.append({
                    "role": "system",
                    "content": f"{header}\n{playbooks}\n{'#' * len(header)}",
                })

        return injections

    @staticmethod
    def _strip_directives(text: str) -> str:
        lines = []
        for line in text.splitlines():
            if line.strip().startswith("[") and line.strip().endswith("]"):
                continue
            lines.append(line)
        return "\n".join(lines).strip()

    @classmethod
    def _detect_phase_label(cls, phase_hint: str, intent: TaskIntent | None) -> str:
        if intent and intent.is_dev_task:
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
