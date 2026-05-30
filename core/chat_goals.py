"""Chat-mode task completion goals — keep turns alive until core work runs.

Refactored to use a registry pattern: ChatGoal is a generic dataclass,
ChatGoalRegistry matches user messages to goal templates via regex, and
ChatGoalGuard blocks misrouted tools based on the active goal's config.

Existing PCAP behavior is preserved via the registered pcap goal builder.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, InitVar
from typing import Any, Callable

from core.task_intent import TaskIntentExtractor


class class_or_instance_method:
    def __init__(self, class_method, instance_property):
        self.class_method = class_method
        self.instance_property = instance_property

    def __get__(self, instance, owner):
        if instance is None:
            return self.class_method.__get__(owner, owner)
        return self.instance_property(instance)


# ──────────────────────────────────────────────────────────────────────────────
# Generic ChatGoal dataclass
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class ChatGoals:
    """Tools that must execute before chat_turn may return."""

    required_tools: list[str] = field(default_factory=list)
    label: str = ""
    verbose: bool = False
    is_from_session: bool = False

    # Tool-specific hints (e.g. pcap_path_hint, filter_expression)
    hints: dict[str, Any] = field(default_factory=dict)

    # Tools that should be blocked while this goal's required_tools
    # haven't completed yet (prevents model from going off-track).
    blocked_tools: list[str] = field(default_factory=list)
    # Message to show when a blocked tool is called.
    blocked_reason: str = ""

    pcap_path_hint: str | None = None
    filter_expression: str | None = None

    def __post_init__(self):
        if self.pcap_path_hint is not None:
            self.hints["pcap_path_hint"] = self.pcap_path_hint
        else:
            self.pcap_path_hint = self.hints.get("pcap_path_hint")

        if self.filter_expression is not None:
            self.hints["filter_expression"] = self.filter_expression
        else:
            self.filter_expression = self.hints.get("filter_expression")

    # ── Convenience class/instance methods (backward compat) ──────────────

    @classmethod
    def from_message(cls, message: str) -> ChatGoals | None:
        return ChatGoalRegistry.match_message(message)

    @classmethod
    def _from_session_class(cls, session: list[dict], message: str) -> ChatGoals | None:
        return ChatGoalRegistry.match_session(session, message)

    def _from_session_instance(self) -> bool:
        return self.is_from_session

    from_session = class_or_instance_method(_from_session_class, _from_session_instance)

    # ── Core API ─────────────────────────────────────────────────────────

    def pending(self, executed: list[str]) -> list[str]:
        done = set(executed)
        return [t for t in self.required_tools if t not in done]

    def nudge_text(self, pending: list[str]) -> str:
        if not pending:
            return ""
        parts = [
            f"[SYSTEM] Task incomplete — {self.label} requires: {', '.join(pending)}.",
        ]
        if self.blocked_reason:
            parts.append(self.blocked_reason)
        parts.append(f"Emit {pending[0]} NOW.")

        # Goal-specific example hints
        if "analyze_pcapng" in pending:
            verb = "true" if self.verbose else "false"
            hint_path = self.pcap_path_hint or "last_capture.pcapng"
            hint_filter = self.filter_expression or "http"
            parts.append(
                f'Example: analyze_pcapng file_path="{hint_path}" '
                f'filter_expression="{hint_filter}" verbose={verb} limit=50'
            )
        elif "port_scan" in pending:
            target = self.hints.get("target", "TARGET_IP")
            parts.append(
                f'Example: port_scan target="{target}"'
            )
        elif "crack_hash" in pending:
            parts.append(
                'Example: crack_hash target_hash="HASH_VALUE"'
            )

        return " ".join(parts)

    def context_directive(self) -> str:
        if not self.from_session:
            return ""
        return self.hints.get("context_directive", "")


# ──────────────────────────────────────────────────────────────────────────────
# Goal Registry
# ──────────────────────────────────────────────────────────────────────────────

_GoalBuilder = Callable[[str, list[dict] | None], ChatGoals | None]

class _GoalEntry:
    def __init__(self, pattern: re.Pattern, builder: _GoalBuilder, priority: int):
        self.pattern = pattern
        self.builder = builder
        self.priority = priority


class ChatGoalRegistry:
    """Match user messages to goal templates via regex patterns."""

    _templates: list[_GoalEntry] = []

    @classmethod
    def register(cls, pattern: str, builder: _GoalBuilder, priority: int = 50):
        cls._templates.append(
            _GoalEntry(re.compile(pattern, re.I), builder, priority)
        )

    @classmethod
    def match_message(cls, message: str) -> ChatGoals | None:
        """Try each registered pattern against the user message."""
        hits: list[tuple[int, _GoalEntry]] = []
        for entry in cls._templates:
            if entry.pattern.search(message):
                hits.append((entry.priority, entry))
        if not hits:
            return None
        # Lowest priority number wins
        hits.sort(key=lambda x: x[0])
        return hits[0][1].builder(message, None)

    @classmethod
    def match_session(cls, messages: list[dict], user_message: str) -> ChatGoals | None:
        """Try session-aware builders for follow-up detection."""
        hits: list[tuple[int, _GoalEntry]] = []
        for entry in cls._templates:
            goal = entry.builder(user_message, messages)
            if goal and goal.from_session:
                hits.append((entry.priority, entry))
        if not hits:
            return None
        hits.sort(key=lambda x: x[0])
        return hits[0][1].builder(user_message, messages)


# ──────────────────────────────────────────────────────────────────────────────
# PCAP Goal Builder (preserves all original behavior)
# ──────────────────────────────────────────────────────────────────────────────

_PCAP_RE = re.compile(
    r"(\.pcapng|\.pcap\b|\btshark\b|\bwireshark\b|last_capture|decode.*packet|"
    r"analyze.*packet|http packet|xmlobj|login.*packet|locate.*pcap)",
    re.I,
)

_FOLLOWUP_DECODE_RE = re.compile(
    r"\b(decode|extract|parse|key\s*values?|look for|find|contents|xmlobj|login|xml)\b",
    re.I,
)


def _build_pcap_filters(lower: str) -> str:
    """Build tshark display filter from user message keywords."""
    filters: list[str] = []
    if "http" in lower:
        filters.append("http")
    if "login" in lower:
        filters.append("http contains login or ftp.request.command == \"USER\" or smtp")
    if "xml" in lower or "xmlobj" in lower:
        filters.append("http contains xml or frame contains xml or xml")

    if not filters and _FOLLOWUP_DECODE_RE.search(lower):
        filters = [
            "http",
            "http contains login or ftp or smtp",
            "http contains xml or frame contains xml",
        ]
    return " or ".join(f"({f})" for f in filters) if filters else "http"


def _build_pcap_goal(message: str, session: list[dict] | None) -> ChatGoals | None:
    """Build a PCAP analysis goal from message (or session follow-up)."""
    lower = (message or "").lower()

    is_followup = _FOLLOWUP_DECODE_RE.search(lower) and not _PCAP_RE.search(lower)

    # ── Session follow-up path ───────────────────────────────────────
    if session is not None and is_followup:
        had_pcap = False
        path_hint = "last_capture.pcapng"
        for msg in reversed((session or [])[-40:]):
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

        verb = True
        return ChatGoals(
            required_tools=["analyze_pcapng"],
            label="PCAP decode follow-up",
            verbose=verb,
            is_from_session=True,
            hints={
                "pcap_path_hint": path_hint,
                "filter_expression": _build_pcap_filters(lower),
                "context_directive": (
                    f"[PCAP CONTEXT] This session already located {path_hint}. "
                    f"The user wants decoded packet fields (login/xml/key values). "
                    f"Use analyze_pcapng(file_path=..., filter_expression=..., verbose=true) — "
                    "NOT encode_decode."
                ),
            },
            blocked_tools=["encode_decode", "append_note", "sequentialthinking"],
            blocked_reason=(
                "Do NOT use encode_decode on PCAP data. "
                "Do NOT declare task complete via append_note."
            ),
        )

    # ── Direct message path ──────────────────────────────────────────
    if not _PCAP_RE.search(lower) and not is_followup:
        return None

    path_hint = None
    m = re.search(r"([\w./\\-]+\.pcapng)", message, re.I)
    if m:
        path_hint = m.group(1).replace("\\", "/")
    elif "last_capture" in lower:
        path_hint = "last_capture.pcapng"

    decode_followup = is_followup

    return ChatGoals(
        required_tools=["analyze_pcapng"],
        label="PCAP decode follow-up" if decode_followup else "PCAP analysis",
        verbose=decode_followup or "decode" in lower,
        hints={
            "pcap_path_hint": path_hint or "last_capture.pcapng",
            "filter_expression": _build_pcap_filters(lower),
        },
        blocked_tools=["encode_decode", "append_note", "sequentialthinking"],
        blocked_reason=(
            "Do NOT use encode_decode on PCAP data. "
            "Do NOT declare task complete via append_note."
        ),
    )


# ──────────────────────────────────────────────────────────────────────────────
# Port Scan Goal Builder
# ──────────────────────────────────────────────────────────────────────────────

def _build_portscan_goal(message: str, session: list[dict] | None) -> ChatGoals | None:
    lower = (message or "").lower()
    target_m = re.search(
        r"\b(\d{1,3}(?:\.\d{1,3}){3})\b|"
        r"\b([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z]{2,})+)\b",
        lower,
    )
    target = target_m.group(0) if target_m else None

    return ChatGoals(
        required_tools=["port_scan"],
        label="Port scan",
        hints={"target": target} if target else {},
        blocked_tools=["append_note"],
        blocked_reason="Complete the port scan before logging notes.",
    )


# ──────────────────────────────────────────────────────────────────────────────
# Hash Crack Goal Builder
# ──────────────────────────────────────────────────────────────────────────────

def _build_hashcrack_goal(message: str, session: list[dict] | None) -> ChatGoals | None:
    lower = (message or "").lower()
    hash_m = re.search(r"\b([a-f0-9]{64})\b", lower)
    hints: dict[str, Any] = {}
    if hash_m:
        hints["target_hash"] = hash_m.group(1)

    return ChatGoals(
        required_tools=["crack_hash"],
        label="Hash cracking",
        hints=hints,
        blocked_tools=["append_note"],
        blocked_reason="Complete the hash crack before logging notes.",
    )


# ──────────────────────────────────────────────────────────────────────────────
# Register all goal templates
# ──────────────────────────────────────────────────────────────────────────────

# PCAP — priority 10 (most specific, many regex patterns)
ChatGoalRegistry.register(
    r"(\.pcapng|\.pcap\b|\btshark\b|\bwireshark\b|last_capture|decode.*packet|"
    r"analyze.*packet|http packet|xmlobj|login.*packet|locate.*pcap|"
    r"\b(?:decode|extract|parse|key\s*values?|look for|contents)\b)",
    _build_pcap_goal,
    priority=10,
)

# Port scan — priority 30
ChatGoalRegistry.register(
    r"(scan.*port|port.*scan|nmap|open port|\bport_scan\b)",
    _build_portscan_goal,
    priority=30,
)

# Hash crack — priority 30
ChatGoalRegistry.register(
    r"(crack.*hash|hash.*crack|brute.*force|password.*hash|\bcrack_hash\b)",
    _build_hashcrack_goal,
    priority=30,
)


# ──────────────────────────────────────────────────────────────────────────────
# ChatGoalGuard — Generic, goal-driven
# ──────────────────────────────────────────────────────────────────────────────

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
            # Block tools that are in the goal's blocked list
            if tool_name in goals.blocked_tools:
                reason = goals.blocked_reason or f"Blocked: complete {goals.label} first."
                if tool_name == "encode_decode" and "pcap" in goals.label.lower():
                    reason = (
                        "Blocked: PCAP content is not base64 text. "
                        "Use analyze_pcapng with verbose=true and a tshark filter."
                    )
                return tool_name, args, reason
        else:
            # Goals completed — block post-completion tool spam
            if tool_name in goals.required_tools:
                return tool_name, args, (
                    f"Blocked: {goals.label} already completed this turn. "
                    "Summarize findings in plain text — do not re-run tools."
                )
            if tool_name in ("sequentialthinking", "find_file"):
                if tool_name in goals.blocked_tools or tool_name == "sequentialthinking":
                    return tool_name, args, (
                        f"Blocked: {goals.label} already completed this turn. "
                        "Summarize findings in plain text — do not re-run tools."
                    )
            if tool_name == "append_note":
                line = str(args.get("line", "")).lower()
                if any(w in line for w in ("task completed", "no valid", "failed", "decoding failed")):
                    return tool_name, args, (
                        "Blocked: do not log false completion notes. Summarize analysis in the response."
                    )

        # General append_note path restriction (regardless of goal)
        if tool_name == "append_note":
            path = str(args.get("path", "")).replace("\\", "/")
            if not TaskIntentExtractor.is_workspace_meta_path(path):
                return tool_name, args, (
                    "Blocked: append_note only on workspace/plan.md, status.md, or session_log.md."
                )

        return tool_name, args, None
