"""Chat-mode task completion goals — keep turns alive until core work runs."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from core.task_intent import TaskIntentExtractor

_PCAP_RE = re.compile(
    r"(\.pcapng|\.pcap\b|\btshark\b|\bwireshark\b|last_capture|decode.*packet|"
    r"analyze.*packet|http packet|xmlobj|login.*packet|locate.*pcap)",
    re.I,
)

_FOLLOWUP_DECODE_RE = re.compile(
    r"\b(decode|extract|parse|key\s*values?|look for|find|contents|xmlobj|login|xml)\b",
    re.I,
)


@dataclass
class ChatGoals:
    """Tools that must execute before chat_turn may return."""

    required_tools: list[str] = field(default_factory=list)
    pcap_path_hint: str | None = None
    filter_expression: str | None = None
    label: str = ""
    verbose: bool = False
    from_session: bool = False

    @classmethod
    def _build_filters(cls, lower: str) -> str:
        filters: list[str] = []
        if "http" in lower:
            filters.append("http")
        if "login" in lower:
            filters.append("http contains login or ftp.request.command == \"USER\" or smtp")
        if "xml" in lower or "xmlobj" in lower:
            filters.append("http contains xml or frame contains xml or xml")

        if not filters and cls._is_decode_followup(lower):
            filters = [
                "http",
                "http contains login or ftp or smtp",
                "http contains xml or frame contains xml",
            ]
        return " or ".join(f"({f})" for f in filters) if filters else "http"

    @staticmethod
    def _is_decode_followup(lower: str) -> bool:
        return bool(_FOLLOWUP_DECODE_RE.search(lower))

    @classmethod
    def from_message(cls, message: str) -> ChatGoals | None:
        lower = (message or "").lower()
        if not _PCAP_RE.search(lower) and not cls._is_decode_followup(lower):
            return None

        path_hint = None
        m = re.search(r"([\w./\\-]+\.pcapng)", message, re.I)
        if m:
            path_hint = m.group(1).replace("\\", "/")
        elif "last_capture" in lower:
            path_hint = "last_capture.pcapng"

        decode_followup = cls._is_decode_followup(lower) and not _PCAP_RE.search(lower)

        return cls(
            required_tools=["analyze_pcapng"],
            pcap_path_hint=path_hint or "last_capture.pcapng",
            filter_expression=cls._build_filters(lower),
            label="PCAP decode follow-up" if decode_followup else "PCAP analysis",
            verbose=decode_followup or "decode" in lower,
        )

    @classmethod
    def from_session(cls, messages: list[dict], user_message: str) -> ChatGoals | None:
        """Detect follow-up decode requests after a prior successful analyze_pcapng."""
        lower = (user_message or "").lower()
        if not cls._is_decode_followup(lower):
            return None

        had_pcap = False
        path_hint = "last_capture.pcapng"
        for msg in reversed(messages[-40:]):
            if msg.get("role") == "tool" and msg.get("name") == "find_file":
                try:
                    data = json.loads(msg.get("content", "{}"))
                    if data.get("recommended"):
                        path_hint = data["recommended"]
                except (json.JSONDecodeError, TypeError):
                    pass
            if msg.get("role") == "tool" and msg.get("name") == "analyze_pcapng":
                try:
                    data = json.loads(msg.get("content", "{}"))
                    if data.get("success"):
                        had_pcap = True
                        break
                except (json.JSONDecodeError, TypeError):
                    pass

        if not had_pcap:
            return None

        return cls(
            required_tools=["analyze_pcapng"],
            pcap_path_hint=path_hint,
            filter_expression=cls._build_filters(lower),
            label="PCAP decode follow-up",
            verbose=True,
            from_session=True,
        )

    def pending(self, executed: list[str]) -> list[str]:
        done = set(executed)
        return [t for t in self.required_tools if t not in done]

    def nudge_text(self, pending: list[str]) -> str:
        if not pending:
            return ""
        parts = [
            f"[SYSTEM] Task incomplete — {self.label} requires: {', '.join(pending)}.",
            "Do NOT use encode_decode on PCAP data. Do NOT declare task complete via append_note.",
            "Emit analyze_pcapng NOW.",
        ]
        if "analyze_pcapng" in pending:
            verb = "true" if self.verbose else "false"
            parts.append(
                f'Example: analyze_pcapng file_path="{self.pcap_path_hint}" '
                f'filter_expression="{self.filter_expression or "http"}" verbose={verb} limit=50'
            )
        return " ".join(parts)

    def context_directive(self) -> str:
        if not self.from_session:
            return ""
        verb = "true" if self.verbose else "false"
        return (
            f"[PCAP CONTEXT] This session already located {self.pcap_path_hint}. "
            f"The user wants decoded packet fields (login/xml/key values). "
            f"Use analyze_pcapng(file_path=..., filter_expression=..., verbose={verb}) — "
            "NOT encode_decode."
        )


class ChatGoalGuard:
    """Block misrouted tools while chat goals are active."""

    @classmethod
    def apply(
        cls,
        tool_name: str,
        args: dict[str, Any],
        goals: ChatGoals | None,
        executed: list[str],
    ) -> tuple[str, dict[str, Any], str | None]:
        if not goals:
            return tool_name, args, None

        pending = goals.pending(executed)

        if pending:
            if tool_name == "encode_decode":
                return tool_name, args, (
                    "Blocked: PCAP content is not base64 text. "
                    "Use analyze_pcapng with verbose=true and a tshark filter."
                )
            if tool_name == "append_note":
                return tool_name, args, (
                    "Blocked: append_note is not task completion. Run analyze_pcapng first."
                )
            if tool_name == "sequentialthinking":
                return tool_name, args, (
                    "Blocked: skip planning — call analyze_pcapng now."
                )
        else:
            if tool_name in ("sequentialthinking", "analyze_pcapng", "find_file", "encode_decode"):
                return tool_name, args, (
                    "Blocked: PCAP analysis already completed this turn. "
                    "Summarize findings in plain text — do not re-run tools."
                )
            if tool_name == "append_note":
                line = str(args.get("line", "")).lower()
                if any(w in line for w in ("task completed", "no valid", "failed", "decoding failed")):
                    return tool_name, args, (
                        "Blocked: do not log false completion notes. Summarize analysis in the response."
                    )

        if tool_name == "append_note":
            path = str(args.get("path", "")).replace("\\", "/")
            if not TaskIntentExtractor.is_workspace_meta_path(path):
                return tool_name, args, (
                    "Blocked: append_note only on workspace/plan.md, status.md, or session_log.md."
                )

        return tool_name, args, None
