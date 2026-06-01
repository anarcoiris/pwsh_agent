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
from core.tool_hints import (
    format_crack_hash_call,
    hash_planning_directive,
    parse_hash_crack_hints,
    parse_pcap_analysis_hints,
    pcap_planning_directive,
)


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

    # Tools that may run multiple times with different arguments (dedup blocks exact repeats).
    iterative_tools: list[str] = field(default_factory=list)

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

    @staticmethod
    def _successful_names(executed: list[Any]) -> set[str]:
        """
        Normalize executed-tool tracking into a set of successful tool names.
        Accepts:
        - ["read_file", "analyze_pcapng"]
        - [{"name": "read_file", "success": True}, ...]
        """
        done: set[str] = set()
        for item in executed or []:
            if isinstance(item, str):
                done.add(item)
                continue
            if isinstance(item, dict):
                name = str(item.get("name", "")).strip()
                if not name:
                    continue
                if item.get("success", True):
                    done.add(name)
        return done

    def pending(self, executed: list[Any]) -> list[str]:
        done = self._successful_names(executed)
        return [t for t in self.required_tools if t not in done]

    def is_pcap_goal(self) -> bool:
        return "pcap" in self.label.lower()

    def allows_iterative(self, tool_name: str) -> bool:
        return tool_name in self.iterative_tools

    def is_workflow_complete(self, executed: list[Any], objective_met: bool = False) -> bool:
        """
        PCAP workflows need multiple analyze_pcapng/read_file passes with different
        filters — do not treat the first analyze_pcapng as turn-complete.
        """
        if self.is_pcap_goal():
            done = self._successful_names(executed)
            if "analyze_pcapng" not in done:
                return False
            return bool(objective_met)
        return not self.pending(executed)

    def may_end_turn(
        self,
        executed: list[Any],
        step: int,
        objective_met: bool = False,
        min_steps: int = 2,
    ) -> bool:
        """Gate turn completion so the agent gets multiple ReAct iterations."""
        if not self.is_workflow_complete(executed, objective_met):
            return False
        if self.is_pcap_goal():
            if step < 2:
                return False
            if not objective_met:
                return False
            # Require at least two substantive PCAP passes (e.g. index + decode/read).
            done = self._successful_names(executed)
            pcap_depth = sum(1 for t in done if t in ("analyze_pcapng", "read_file"))
            return pcap_depth >= 2
        return step >= min_steps

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
            parts.append(f"Example: {format_crack_hash_call(self.hints)}")
            if self.hints.get("salt"):
                parts.append("You MUST pass salt= (appended to password before hashing).")

        return " ".join(parts)

    def context_directive(self) -> str:
        return str(self.hints.get("context_directive", "") or "")


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
    r"analyze.*packet|http packet|login.*packet|locate.*pcap)",
    re.I,
)

_HASH_CRACK_RE = re.compile(
    r"(crack.*(?:sha-?)?256|(?:sha-?)?256.*hash|hash.*crack|brute.*force|\bcrack_hash\b|"
    r"\bhaspro\b|\bhashpro\b|\bhash_pro7\b)",
    re.I,
)

_FOLLOWUP_DECODE_RE = re.compile(
    r"\b(decode|extract|parse|key\s*values?|look for|find|contents|login|expand|search|grep|filter)\b",
    re.I,
)

_CREDENTIAL_SESSION_RE = re.compile(
    r"\b(expand.*search|search.*term|grep|filter|password|xml|xmlobj|login|salt|verbose|credential|"
    r"complete.*previous|previous task|analyze.*filter)\b",
    re.I,
)

_DISPLAY_FACTS_RE = re.compile(
    r"\b(show|display|tell|list|what are|give me)\b.*\b(facts?|hash|salt|cracked|credential)\b",
    re.I,
)

_ACTIONABLE_HASH_RE = re.compile(
    r"\b(extract|crack|use|write|save|create|find|analy[sz]e|try again|hashpro|pwd[_\d]*\.txt)\b",
    re.I,
)


def _is_display_only_hash_request(message: str) -> bool:
    """True only for read-only facts requests, not actionable extraction/cracking tasks."""
    text = message or ""
    if not _DISPLAY_FACTS_RE.search(text):
        return False
    if _ACTIONABLE_HASH_RE.search(text):
        return False
    return True


def _build_pcap_filters(lower: str) -> str:
    """Build tshark display filter from user message keywords."""
    return parse_pcap_analysis_hints(lower).get("filter_expression", "http")


def _build_pcap_goal(message: str, session: list[dict] | None) -> ChatGoals | None:
    """Build a PCAP analysis goal from message (or session follow-up)."""
    lower = (message or "").lower()

    if _HASH_CRACK_RE.search(lower):
        return None

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
            iterative_tools=["analyze_pcapng", "read_file", "find_file"],
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
            blocked_tools=["encode_decode", "sequentialthinking"],
            blocked_reason="Do NOT use encode_decode on PCAP data.",
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
    pcap_hints = parse_pcap_analysis_hints(message)
    path = path_hint or "last_capture.pcapng"

    return ChatGoals(
        required_tools=["analyze_pcapng"],
        iterative_tools=["analyze_pcapng", "read_file", "find_file"],
        label="PCAP decode follow-up" if decode_followup else "PCAP analysis",
        verbose=pcap_hints.get("verbose", decode_followup or "decode" in lower),
        hints={
            "pcap_path_hint": path,
            "filter_expression": pcap_hints.get("filter_expression", "http"),
            "context_directive": pcap_planning_directive(pcap_hints, path),
        },
        blocked_tools=["encode_decode", "sequentialthinking"],
        blocked_reason="Do NOT use encode_decode on PCAP data.",
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
        blocked_tools=[],
        blocked_reason="",
    )


# ──────────────────────────────────────────────────────────────────────────────
# Hash Crack Goal Builder
# ──────────────────────────────────────────────────────────────────────────────

def _build_hashcrack_goal(message: str, session: list[dict] | None) -> ChatGoals | None:
    hints = parse_hash_crack_hints(message)
    lower = (message or "").lower()

    # Display-only: show facts/hash/salt — read session artifacts, do not re-crack blindly
    if _is_display_only_hash_request(message or ""):
        # #region agent log
        try:
            from core.debug_log import debug_log
            debug_log(
                "chat_goals.py:_build_hashcrack_goal",
                "display-only hash request",
                {"message_head": (message or "")[:180]},
                "H2",
            )
        except Exception:
            pass
        # #endregion
        return ChatGoals(
            required_tools=["read_file"],
            iterative_tools=["read_file", "find_file"],
            label="Display session facts",
            blocked_tools=["sequentialthinking", "crack_hash"],
            blocked_reason=(
                "User asked to SHOW facts — read_file state/sessions/<id>/facts.json or "
                "workspace/sessions/<id>/login_forms.txt first; do not run crack_hash until asked."
            ),
            hints={
                "context_directive": (
                    "[DISPLAY FACTS] Read session facts.json and login_forms.txt, then summarize "
                    "hash, salt, user/password, xmlObj from disk — no placeholders."
                ),
            },
        )
    elif _DISPLAY_FACTS_RE.search(message or ""):
        # #region agent log
        try:
            from core.debug_log import debug_log
            debug_log(
                "chat_goals.py:_build_hashcrack_goal",
                "display-intent skipped due actionable request",
                {"message_head": (message or "")[:180]},
                "H2",
            )
        except Exception:
            pass
        # #endregion

    if not hints.get("target_hash") and not re.search(
        r"(crack|hash|sha-?256|hashpro|haspro)", (message or ""), re.I
    ):
        return None

    hints["context_directive"] = hash_planning_directive(hints)

    # Multi-step: extract from PCAP/reports → deliverable file → crack_hash
    if re.search(
        r"\b("
        r"pwd(?:_[\d]+)?\.txt|login_forms\.txt|credentials?\.txt|"
        r"save.*values|write.*\b(?:pwd|login_forms|credentials?)\b|"
        r"extract.*(?:pcap|password|salt)|"
        r"list.*(?:password|hash|salt)|"
        r"find.*report|read.*report|read.*plan|"
        r"last_capture(?:\.pcapng)?|pcap(?:ng)?"
        r")\b",
        lower,
    ):
        deliverables = TaskIntentExtractor._extract_deliverables(message)
        out_path = "pwd.txt"
        for d in deliverables:
            if "login_forms" in d.lower():
                out_path = d
                break
        else:
            for d in deliverables:
                if d.endswith(".txt"):
                    out_path = d
                    break
        hints["deliverable_path"] = out_path
        xml_hint = ""
        if re.search(r"\bxml\b|xmlobj|salt", lower):
            xml_hint = (
                " For XML/salt: find_and_grep(pattern='xml|Password|Username|616a6178|xmlObj', "
                "path_glob='.pulse/pcap_logs/verbose_*.txt', case_insensitive=true, max_files=10) "
                "— searches multiple verbose logs (hex-encoded fields need broad patterns like 'xml', not only 'xmlObj'). "
                "If http filter missed salt, re-run analyze_pcapng with filter_expression='xml'."
            )
        hints["context_directive"] = (
            hints.get("context_directive", "")
            + f"\n[ROADMAP] 1) find_file('report_*.md') + read_file, or analyze_pcapng on last_capture.pcapng (verbose=true; try filter 'xml' if 'http' misses salt) "
            f"2) find_and_grep across verbose logs (NOT single-file grep only) "
            f"3) crack_hash with each Password hash + its xmlObj salt (sha256(password+salt)) "
            f"4) write_file path='{out_path}' with hash, salt, AND cracked plaintext results.{xml_hint}"
        )
        # #region agent log
        try:
            from core.debug_log import debug_log
            debug_log(
                "chat_goals.py:_build_hashcrack_goal",
                "multi-step hash goal",
                {"out_path": out_path, "deliverables": deliverables},
                "H3",
            )
        except Exception:
            pass
        # #endregion
        iterative = ["read_file", "analyze_pcapng", "write_file", "find_file"]
        if re.search(r"\bxml\b|xmlobj|salt", lower):
            iterative.extend(["grep_file", "find_and_grep"])
        return ChatGoals(
            required_tools=["read_file", "analyze_pcapng", "crack_hash", "write_file"],
            iterative_tools=iterative,
            label="Extract credentials and crack hash",
            hints=hints,
            blocked_tools=["encode_decode"],
            blocked_reason="Use read_file/analyze_pcapng/grep_file for real values — not encode_decode.",
        )

    # #region agent log
    try:
        from core.debug_log import debug_log
        debug_log(
            "chat_goals.py:_build_hashcrack_goal",
            "simple hash goal",
            {"message_head": (message or "")[:180], "target_hash_present": bool(hints.get("target_hash"))},
            "H3",
        )
    except Exception:
        pass
    # #endregion
    return ChatGoals(
        required_tools=["crack_hash"],
        label="Hash cracking",
        hints=hints,
        blocked_tools=["analyze_pcapng", "encode_decode"],
        blocked_reason="Hash task — plan salt+mask, then crack_hash only.",
    )


def _session_had_credential_work(session: list[dict]) -> bool:
    for msg in reversed((session or [])[-50:]):
        if msg.get("role") != "tool":
            continue
        if msg.get("name") in (
            "analyze_pcapng", "grep_file", "find_and_grep",
            "crack_hash", "write_file", "read_file",
        ):
            return True
    return False


def _build_credential_session_goal(message: str, session: list[dict] | None) -> ChatGoals | None:
    """Follow-up turns that continue PCAP/credential extraction without re-stating crack/hash."""
    if session is None:
        return None
    if not _CREDENTIAL_SESSION_RE.search(message or ""):
        return None
    if not _session_had_credential_work(session):
        return None

    return ChatGoals(
        required_tools=["find_and_grep"],
        iterative_tools=["find_and_grep", "grep_file", "read_file", "analyze_pcapng", "write_file", "crack_hash"],
        label="Credential extract follow-up",
        is_from_session=True,
        blocked_tools=["sequentialthinking"],
        blocked_reason=(
            "Do NOT plan in prose or declare task complete — call find_and_grep across "
            ".pulse/pcap_logs/verbose_*.txt with pattern "
            "'password|Password|Username|xml|xmlObj|616a6178|login'."
        ),
        hints={
            "context_directive": (
                "[CREDENTIAL FOLLOW-UP] Prior session has PCAP/credential work. "
                "Use find_and_grep(pattern='password|Password|Username|xml|xmlObj|616a6178|login', "
                "path_glob='.pulse/pcap_logs/verbose_*.txt', case_insensitive=true, max_files=10). "
                "Then write_file login_forms.txt with REAL values and crack_hash if needed."
            ),
        },
    )


# ──────────────────────────────────────────────────────────────────────────────
# Register all goal templates
# ──────────────────────────────────────────────────────────────────────────────

# PCAP — priority 10 (most specific, many regex patterns)
ChatGoalRegistry.register(
    r"(\.pcapng|\.pcap\b|\btshark\b|\bwireshark\b|last_capture|decode.*packet|"
    r"analyze.*packet|http packet|login.*packet|locate.*pcap|"
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

# Hash crack — priority 5 (beats PCAP false-positives on xml/login tokens in salts)
ChatGoalRegistry.register(
    r"(crack.*hash|hash.*crack|brute.*force|password.*hash|\bcrack_hash\b|"
    r"crack.*sha-?256|sha-?256.*crack|\bhaspro\b|\bhashpro\b)",
    _build_hashcrack_goal,
    priority=5,
)

# Credential session follow-up — session-only (pattern never matches match_message)
ChatGoalRegistry.register(
    r"(?!)",
    _build_credential_session_goal,
    priority=7,
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
        *,
        strategy_note: bool = False,
    ) -> tuple[str, dict[str, Any], str | None]:
        if not goals:
            return tool_name, args, None

        pending = goals.pending(executed)
        done = goals._successful_names(executed)

        if pending:
            if tool_name == "sequentialthinking":
                # Allow one planning thought, then force action tools.
                if "sequentialthinking" in done:
                    return tool_name, args, (
                        f"Blocked: {goals.label} requires action tools ({', '.join(pending)}). "
                        "Do NOT use sequentialthinking again — emit find_and_grep, read_file, or analyze_pcapng."
                    )
                return tool_name, args, None
            if tool_name == "append_note":
                if strategy_note:
                    return tool_name, args, None
                if "write_file" in pending:
                    deliverable = goals.hints.get("deliverable_path", "login_forms.txt")
                    return tool_name, args, (
                        f"Blocked: deliverable '{deliverable}' not written yet. "
                        "Parse http_forms from the last analyze_pcapng result and "
                        f"write_file(path='{deliverable}', content=<REAL values>) — "
                        "no more strategy append_note until the file exists."
                    )
                return tool_name, args, (
                    f"Blocked: {goals.label} still pending ({', '.join(pending)}). "
                    f"Run {pending[0]} before writing progress notes."
                    + (
                        f" Example: {format_crack_hash_call(goals.hints)}"
                        if pending[0] == "crack_hash"
                        else ""
                    )
                )
            if tool_name == "crack_hash" and goals.hints.get("salt") and not args.get("salt"):
                return tool_name, args, (
                    f"Blocked: user specified salt '{goals.hints['salt']}' — "
                    "pass salt= in crack_hash (concatenated to password before SHA-256)."
                )
            if tool_name == "write_file" and "crack_hash" in pending:
                if goals.label == "Extract credentials and crack hash":
                    deliverable = goals.hints.get("deliverable_path", "pwd.txt")
                    return tool_name, args, (
                        f"Blocked: run crack_hash before writing '{deliverable}'. "
                        "Extract hash+salt pairs from http_forms and login_token xmlObj, "
                        "then crack_hash(target_hash=..., salt=..., mask='NNNNNNAA!')."
                    )
            if tool_name == "read_file":
                p = str(args.get("path", "")).replace("\\", "/").lower()
                if "/artifacts/" in p and re.search(r"/(read_file|grep_file|analyze_pcapng)_\d", p):
                    return tool_name, args, (
                        "Blocked: do not read spilled tool artifacts. "
                        "Use analyze_pcapng output, verbose_log_file, or find_and_grep on "
                        ".pulse/pcap_logs/verbose_*.txt."
                    )
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
            # #region agent log
            if tool_name == "analyze_pcapng":
                try:
                    from core.debug_log import debug_log
                    debug_log(
                        "chat_goals.py:ChatGoalGuard",
                        "post-minimum tool check",
                        {
                            "tool": tool_name,
                            "allows_iterative": goals.allows_iterative(tool_name),
                            "pending": pending,
                            "executed": executed,
                        },
                        "G",
                        "run1",
                    )
                except Exception:
                    pass
            # #endregion
            # Goals minimum met — allow iterative PCAP tools with different args (dedup handles exact repeats)
            if tool_name in goals.required_tools and not goals.allows_iterative(tool_name):
                return tool_name, args, (
                    f"Blocked: {goals.label} already completed this turn. "
                    "Summarize findings in plain text — do not re-run tools."
                )
            if tool_name == "sequentialthinking":
                return tool_name, args, (
                    f"Blocked: {goals.label} already completed this turn. "
                    "Summarize findings in plain text — do not re-run tools."
                )
            if tool_name == "find_file" and not goals.allows_iterative(tool_name):
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
            from core.session_paths import is_session_note_path
            path = str(args.get("path", "")).replace("\\", "/")
            if not TaskIntentExtractor.is_workspace_meta_path(path) and not is_session_note_path(path):
                return tool_name, args, (
                    "Blocked: append_note only on session plan/status/log or scratchpads/*.md."
                )

        return tool_name, args, None
