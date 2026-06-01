"""
core/llm_utils.py — Shared reasoning utilities for Pulse Windows Agent.

Ported and adapted from MCP_Pentesting/core/llm_utils.py.

Includes:
- ArgumentNormalizer   — sanitise/normalise tool args before dispatch
- ResultCompactor      — tool-aware truncation to prevent context bloat
- RetryOrchestrator    — parser_reflection() self-correction thought injection
- DynamicContextBuilder — phase-aware context hints injected at each LLM turn
- SequentialThinkingEngine — full implementation with branches, needsMoreThoughts
"""

import json
import logging
import re
import sys
from typing import Any

logger = logging.getLogger("pwsh_agent.core.llm_utils")


# ──────────────────────────────────────────────────────────────────────────────
# Argument Normalizer
# ──────────────────────────────────────────────────────────────────────────────

class ArgumentNormalizer:
    """Validates and normalises tool arguments before execution."""

    @staticmethod
    def normalize(tool_name: str, args: dict) -> dict:
        """Apply tool-specific argument normalisation."""
        args = {k: v.strip() if isinstance(v, str) else v for k, v in args.items()}

        # Remove protocol prefix for network targets
        if tool_name in ("port_scan", "ping_sweep", "dns_lookup"):
            target = args.get("target", args.get("hostname", args.get("cidr", "")))
            target = re.sub(r"^https?://", "", str(target))
            # If target has port embedded (host:port) split it
            if ":" in target and not target.startswith("["):
                host, port = target.rsplit(":", 1)
                if port.isdigit():
                    args["target"] = host.split("/")[0]
                    if "ports" not in args:
                        args["ports"] = port
                else:
                    args["target"] = target.split("/")[0]
            else:
                if "target" in args:
                    args["target"] = target.split("/")[0]
                elif "hostname" in args:
                    args["hostname"] = target.split("/")[0]
                elif "cidr" in args:
                    args["cidr"] = target

        # Ensure URLs have a scheme for web tools
        elif tool_name in ("http_headers_check",):
            url = args.get("url", "")
            if url and not url.startswith(("http://", "https://")):
                args["url"] = f"https://{url}"

        # Strip shell prompt characters from commands
        elif tool_name == "host_exec":
            cmd = args.get("command", "")
            cmd = re.sub(r"^\s*[$#>]\s*", "", cmd)
            args["command"] = cmd

        return args


# ──────────────────────────────────────────────────────────────────────────────
# Result Compactor
# ──────────────────────────────────────────────────────────────────────────────

class ResultCompactor:
    """Intelligently compresses tool outputs while preserving semantic value."""

    MAX_CHARS: int = 22_000

    @classmethod
    def configure_max_chars(cls, max_chars: int) -> None:
        cls.MAX_CHARS = max(8_000, int(max_chars))

    @classmethod
    def compact(cls, tool_name: str, result: str) -> str:
        if len(result) <= cls.MAX_CHARS:
            return result

        compactors = {
            "port_scan":          cls._compact_port_scan,
            "ping_sweep":         cls._compact_ping_sweep,
            "http_headers_check": cls._compact_http,
            "analyze_pcapng":     cls._compact_pcap,
            "host_exec":          cls._generic_compact,
            "system_info":        cls._generic_compact,
            "cve_lookup":         cls._generic_compact,
        }
        return compactors.get(tool_name, cls._generic_compact)(result)

    # ── Tool-specific compactors ───────────────────────────────────────────

    @classmethod
    def _compact_port_scan(cls, result: str) -> str:
        try:
            data = json.loads(result)
            # Already structured — filter to open ports only
            if "open_ports" in data:
                open_ports = data["open_ports"]
                total = len(data.get("all_ports", open_ports))
                return (
                    f"[COMPACTED: {len(open_ports)} open / {total} scanned]\n"
                    + json.dumps({"open_ports": open_ports[:100]}, indent=2)
                )
        except (json.JSONDecodeError, ValueError, KeyError):
            pass
        # Text fallback — keep lines mentioning open
        lines = result.split("\n")
        kept = [l for l in lines if "open" in l.lower() or "port" in l.lower()]
        return (
            f"[COMPACTED from {len(lines)} to {len(kept)} lines]\n"
            + "\n".join(kept[:200])
        )

    @classmethod
    def _compact_ping_sweep(cls, result: str) -> str:
        try:
            data = json.loads(result)
            if "live_hosts" in data:
                hosts = data["live_hosts"]
                ips = [h.get("IP", h) for h in hosts]
                return (
                    f"[PING SWEEP: {len(hosts)} live hosts]\n"
                    f"Active IPs: {', '.join(str(ip) for ip in ips[:200])}"
                )
        except (json.JSONDecodeError, ValueError, KeyError):
            pass
        return cls._generic_compact(result)

    @classmethod
    def _compact_http(cls, result: str) -> str:
        try:
            data = json.loads(result)
            summary = {
                "status_code": data.get("status_code"),
                "security_notes": data.get("security_notes", []),
                "server": data.get("headers", {}).get("Server"),
                "content_type": data.get("headers", {}).get("Content-Type"),
            }
            return (
                f"[HTTP HEADERS COMPACTED — full headers omitted]\n"
                + json.dumps(summary, indent=2)
            )
        except (json.JSONDecodeError, ValueError, KeyError):
            return cls._generic_compact(result)

    @classmethod
    def _compact_pcap(cls, result: str) -> str:
        try:
            data = json.loads(result)
            analysis = data.get("analysis", data)

            # Priority order for compaction: preserve key_fields and
            # verbose_log_file metadata at full fidelity (they're already
            # compact).  Truncate the large text blobs proportionally.
            _PRIORITY_KEYS = [
                "key_fields",
                "potential_plaintext_credentials",
                "http_forms",
                "http_index",
                "extracted_secrets",
                "extracted_keywords",
            ]
            _LARGE_KEYS = ["packet_summary", "protocol_hierarchy", "ip_conversations"]
            _META_KEYS = ["verbose_log_file", "verbose_log_lines", "verbose_log_bytes",
                          "packet_summary_error"]

            compact: dict = {}
            budget = cls.MAX_CHARS - 200  # room for JSON envelope

            # Pass 1: keep priority + meta fields (small/important)
            for k in _PRIORITY_KEYS + _META_KEYS:
                if k in analysis:
                    v = analysis[k]
                    s = json.dumps(v, default=str)
                    if len(s) < 15_000:
                        compact[k] = v
                        budget -= len(s)
                    else:
                        compact[k] = v[:15_000] if isinstance(v, str) else v
                        budget -= 15_000

            # Pass 2: large text blobs — allocate remaining budget evenly
            large_present = [k for k in _LARGE_KEYS if k in analysis]
            if large_present and budget > 0:
                per_key = max(2_000, budget // len(large_present))
                for k in large_present:
                    v = analysis[k]
                    if isinstance(v, str) and len(v) > per_key:
                        compact[k] = v[:per_key] + f"\n[... truncated from {len(v)} chars]"
                    else:
                        compact[k] = v

            return (
                "[PCAP ANALYSIS COMPACTED]\n"
                + json.dumps(compact, indent=2)[:cls.MAX_CHARS]
            )
        except Exception:
            return cls._generic_compact(result)

    @staticmethod
    def _generic_compact(result: str) -> str:
        lines = result.split("\n")
        if len(lines) <= 100:
            return result[:30_000]

        head = lines[:30]
        tail = lines[-20:]
        middle = lines[30:-20]

        # Prioritise lines with interesting security keywords
        interesting = [
            l for l in middle
            if len(l.strip()) > 10
            and any(
                kw in l.lower()
                for kw in [
                    ":", "=", "error", "found", "open", "vuln", "cve",
                    "host", "exploit", "password", "user", "path",
                    "critical", "high", "warning", "admin",
                ]
            )
        ]
        mid_sample = (
            interesting[:100]
            if interesting
            else middle[:: max(1, len(middle) // 50)][:50]
        )

        return "\n".join(
            head
            + [f"\n--- [{len(middle)} middle lines → {len(mid_sample)} kept] ---\n"]
            + mid_sample
            + ["\n--- [end] ---\n"]
            + tail
        )


# ──────────────────────────────────────────────────────────────────────────────
# Retry Orchestrator (Parser Reflection)
# ──────────────────────────────────────────────────────────────────────────────

class RetryOrchestrator:
    """Manages automatic self-correction when the LLM produces no tool calls."""

    MAX_REFLECTIONS = 5  # hard cap on consecutive reflections before giving up

    def __init__(self):
        self._parse_fail_count: int = 0

    def parser_reflection(
        self,
        raw_content: str,
        parser=None,
        *,
        session_id: str | None = None,
    ) -> dict | None:
        """
        Return a synthetic sequentialthinking tool_call that forces the LLM
        to self-diagnose why it didn't emit a valid tool call, then plan the
        correct next step.

        If *parser* is provided, first attempt to salvage a real tool call
        from the raw JSON text the model emitted as prose.

        Returns None once MAX_REFLECTIONS is exceeded (caller should inject
        a hard stall nudge instead).
        """
        # Salvage path: model wrote valid tool JSON as plain text
        if parser is not None:
            salvaged = parser.salvage_tool_call(raw_content)
            if salvaged:
                logger.info("parser_reflection: salvaged tool call %s", salvaged["function"]["name"])
                self._parse_fail_count = 0
                return salvaged

        # Intent salvage: map user/action prose to a concrete tool (avoid thinking loops)
        try:
            from core.intent_salvage import salvage_intent_tool_call
            intent_call = salvage_intent_tool_call(
                raw_content,
                getattr(parser, "_user_context", "") if parser else "",
                session_id=session_id,
            )
            if intent_call:
                logger.info(
                    "parser_reflection: intent-salvaged tool call %s",
                    intent_call["function"]["name"],
                )
                self._parse_fail_count = 0
                return intent_call
        except Exception:
            pass

        self._parse_fail_count += 1
        if self._parse_fail_count > self.MAX_REFLECTIONS:
            logger.warning(
                "parser_reflection: MAX_REFLECTIONS (%d) hit — stopping reflection loop",
                self.MAX_REFLECTIONS,
            )
            return None

        snippet = raw_content[:400].strip()
        user_ctx = getattr(parser, "_user_context", "") if parser else ""
        from core.task_intent import detect_mission_kind, extract_filename_globs

        kind = detect_mission_kind(user_ctx)
        if kind == "file_find":
            globs = extract_filename_globs(user_ctx) or ["*.txt"]
            example = (
                f'<tool_call>\n{{"name": "find_file", "arguments": {{"name": "{globs[0]}"}}}}\n</tool_call>'
            )
        else:
            example = (
                '<tool_call>\n{"name": "find_and_grep", "arguments": {"pattern": "xml|Password", '
                '"path_glob": ".pulse/pcap_logs/verbose_*.txt", "case_insensitive": true}}\n</tool_call>'
            )
        thought = (
            f"Parse failure #{self._parse_fail_count} detected. "
            f"My previous turn produced output that could not be mapped to any registered tool call.\n"
            f"Raw output snippet: {snippet!r}\n\n"
            f"Action plan:\n"
            f"  1. Identify the intent expressed in the output.\n"
            f"  2. Map that intent to the closest registered tool (host_exec, dns_lookup, port_scan, etc.).\n"
            f"  3. Emit the correct tool call using <tool_call> XML tags (required format). Example:\n"
            f"{example}\n"
            f"  4. DO NOT emit [SYSTEM] Task complete, [STATUS], or **Next Steps** prose — ONLY a tool call."
        )
        return {
            "function": {
                "name": "sequentialthinking",
                "arguments": {
                    "thought": thought,
                    "thoughtNumber": 1,
                    "totalThoughts": 2,
                    "nextThoughtNeeded": True,
                    "isRevision": False,
                    "branchId": f"parser_reflection_{self._parse_fail_count}",
                },
            }
        }

    def reset(self):
        self._parse_fail_count = 0


# ──────────────────────────────────────────────────────────────────────────────
# Dynamic Context Builder
# ──────────────────────────────────────────────────────────────────────────────

class DynamicContextBuilder:
    """
    Injects a phase-aware context hint into the message list at each LLM turn.
    Adapted for Windows recon/enum/analysis phases (no Kali-specific tools).
    """

    _RECON_TOOLS  = {"dns_lookup", "ping_sweep", "system_info"}
    _ENUM_TOOLS   = {"port_scan", "http_headers_check", "ssl_analysis", "cve_lookup"}
    _REPORT_TOOLS = {"finding_create", "finding_list"}

    @classmethod
    def build_context(cls, messages: list, anchor_query: str | None = None) -> str:
        from core.query_anchor import resolve_anchor_query
        from core.task_intent import detect_mission_kind

        latest = (anchor_query or "").strip() or resolve_anchor_query(messages)
        latest_lower = latest.lower()
        kind = detect_mission_kind(latest)

        hash_task = kind == "hash"
        pcap_task = kind == "pcap"

        # #region agent log
        try:
            from core.debug_log import debug_log
            debug_log(
                "llm_utils.py:build_context",
                "phase detection",
                {"pcap_task": pcap_task, "hash_task": hash_task, "query_head": latest_lower[:100]},
                "C",
            )
        except Exception:
            pass
        # #endregion

        if hash_task:
            from core.tool_hints import parse_hash_crack_hints, hash_planning_directive

            hints = parse_hash_crack_hints(latest)
            return (
                "\n[CURRENT PHASE: HASH CRACKING]\n"
                + hash_planning_directive(hints)
                + "\n"
            )

        if pcap_task:
            from core.tool_hints import parse_pcap_analysis_hints, pcap_planning_directive

            pcap_hints = parse_pcap_analysis_hints(latest)
            return (
                "\n[CURRENT PHASE: PCAP ANALYSIS]\n"
                + pcap_planning_directive(pcap_hints)
                + "\n"
            )

        # Development / file tasks — override default recon bias
        if kind == "dev":
            return (
                "\n[CURRENT PHASE: DEVELOPMENT / FILE TASK]\n"
                "The user wants coding, reading, or writing files — NOT network recon.\n"
                "Use read_file, write_file, run_script, and host_exec (PowerShell only) — NOT raw python via host_exec for .py files.\n"
                "Do NOT run port_scan, dns_lookup, ping_sweep, system_info, or http_headers_check "
                "unless the user explicitly requests network activity.\n"
                "In chat mode: do NOT declare MISSION_COMPLETE or generate engagement reports.\n"
            )

        if kind == "file_find":
            from core.task_intent import extract_filename_globs

            globs = extract_filename_globs(latest) or ["<glob>.txt"]
            glob_hint = ", ".join(f"find_file('{g}')" for g in globs[:3])
            phase = (
                "\n[CURRENT PHASE: FILE DISCOVERY]\n"
                "Locate files by NAME under workspace and app install roots (search_roots) — "
                "NOT under .pulse unless the user named that path.\n"
                f"Workflow: {glob_hint} → read_file(path=<recommended>) for each hit.\n"
                "Do NOT use find_and_grep for filename discovery (it greps file CONTENTS, not names).\n"
                "Do NOT default path_glob to .pulse/pcap_logs — that is only for PCAP verbose logs.\n"
            )
            # #region agent log
            try:
                from core.debug_log import debug_log_session
                debug_log_session(
                    "05286d",
                    "llm_utils.py:build_context",
                    "file_find phase",
                    {"globs": globs, "query_head": latest[:120]},
                    "A",
                )
            except Exception:
                pass
            # #endregion
            return phase

        from core.intent_salvage import _SEARCH_INTENT_RE
        from core.task_intent import is_file_discovery_mission

        if _SEARCH_INTENT_RE.search(latest_lower) and not is_file_discovery_mission(latest):
            return (
                "\n[CURRENT PHASE: CREDENTIAL / LOG SEARCH]\n"
                "Prior PCAP or credential work is active — NOT generic recon.\n"
                "Use find_and_grep across .pulse/pcap_logs/verbose_*.txt "
                "(pattern: password|Password|Username|xml|xmlObj|login).\n"
                "Do NOT emit [SYSTEM] Task complete or **Next Steps** prose — emit a <tool_call> block.\n"
            )

        tools_used = {m.get("name", "") for m in messages if m.get("role") == "tool"}

        has_recon  = bool(tools_used & cls._RECON_TOOLS)
        has_enum   = bool(tools_used & cls._ENUM_TOOLS)
        has_report = bool(tools_used & cls._REPORT_TOOLS)

        # General / conversational / analysis intent — do not force recon tools.
        if kind == "general" and not has_recon and not has_enum and not has_report:
            return (
                "\n[CURRENT PHASE: GENERAL / ANALYSIS]\n"
                "This is a conversational, planning, or review request.\n"
                "Respond directly in plain text. Tool use is optional — "
                "only call a tool if it directly serves the user's question.\n"
                "Do NOT force network recon tools (port_scan, dns_lookup, ping_sweep) "
                "unless the user explicitly asks for them.\n"
            )

        if has_report:
            return (
                "\n[CURRENT PHASE: ANALYSIS & REPORTING]\n"
                "You have gathered enough data. "
                "Use finding_list to review findings, then report_generate for the final report.\n"
            )
        if has_enum:
            return (
                "\n[CURRENT PHASE: ENUMERATION]\n"
                "Port/service data available. "
                "Deepen with: http_headers_check, ssl_analysis, cve_lookup, encode_decode.\n"
                "Register significant findings with finding_create.\n"
            )
        if has_recon:
            return (
                "\n[CURRENT PHASE: ACTIVE SCANNING]\n"
                "Initial recon complete. "
                "Now run: port_scan, http_headers_check, ssl_analysis on discovered targets.\n"
            )
        # No tools used yet
        return (
            "\n[CURRENT PHASE: RECONNAISSANCE]\n"
            "No recon performed yet. "
            "Start with: system_info, dns_lookup, ping_sweep.\n"
            "Do NOT emit plain text — call a tool immediately.\n"
        )


# ──────────────────────────────────────────────────────────────────────────────
# Sequential Thinking Engine (full father-repo version, adapted)
# ──────────────────────────────────────────────────────────────────────────────

class SequentialThinkingEngine:
    """
    Stateful engine for sequential thinking tool logic.

    Supports:
    - Linear thought chains
    - Revisions (isRevision + revisesThought)
    - Branching (branchId + branchFromThought)
    - Dynamic chain extension (needsMoreThoughts)
    - Budget guardrail (max_thoughts)
    - ANSI console rendering with branch/revision colouring
    """

    DEFAULT_MAX_THOUGHTS: int = 15

    def __init__(self, max_thoughts: int = DEFAULT_MAX_THOUGHTS):
        self.thought_history: list[dict] = []
        self.branches: dict[str, list[dict]] = {}
        self.max_thoughts = max_thoughts
        self._total_processed: int = 0

    # ── Core processing ───────────────────────────────────────────────────

    def process_thought(self, args: dict) -> dict:
        """Process a single thought step. Returns MCP-shaped result dict."""
        if self._total_processed >= self.max_thoughts:
            logger.warning(
                "SequentialThinkingEngine: budget (%d) exceeded — halting chain.",
                self.max_thoughts,
            )
            return {
                "thoughtNumber": args.get("thoughtNumber", self._total_processed + 1),
                "totalThoughts": self._total_processed,
                "status": "budget_exceeded",
                "error": (
                    f"Thought budget ({self.max_thoughts}) exhausted. "
                    "Synthesise your findings now."
                ),
            }

        thought          = args.get("thought", "")
        thought_num      = args.get("thoughtNumber", 1)
        total_thoughts   = args.get("totalThoughts", 1)
        is_revision      = args.get("isRevision", False)
        revises_thought  = args.get("revisesThought", None)
        branch_from      = args.get("branchFromThought", None)
        branch_id        = args.get("branchId", "")
        needs_more       = args.get("needsMoreThoughts", False)
        next_needed      = args.get("nextThoughtNeeded", False)

        # Dynamically extend declared total if chain needs to grow
        if needs_more and next_needed:
            total_thoughts = max(total_thoughts, thought_num + 1)

        record = {
            "thoughtNumber":    thought_num,
            "totalThoughts":    total_thoughts,
            "thought":          thought,
            "isRevision":       is_revision,
            "revisesThought":   revises_thought,
            "branchFromThought": branch_from,
            "branchId":         branch_id,
        }
        self.thought_history.append(record)
        self._total_processed += 1

        if branch_id:
            self.branches.setdefault(branch_id, []).append(record)

        self._render_console(thought, thought_num, total_thoughts,
                             is_revision, revises_thought, branch_id)

        result = {
            "thoughtNumber":   thought_num,
            "totalThoughts":   total_thoughts,
            "nextThoughtNeeded": next_needed,
            "status":          "success",
        }
        # #region agent log
        try:
            from core.debug_log import debug_log_session
            debug_log_session(
                "5a1f5b",
                "llm_utils.py:process_thought",
                "sequentialthinking result",
                {
                    "thought_num": thought_num,
                    "total_thoughts": total_thoughts,
                    "next_needed_in": next_needed,
                    "next_needed_out": result["nextThoughtNeeded"],
                    "history_len": len(self.thought_history),
                    "budget": self.max_thoughts,
                },
                "A",
            )
        except Exception:
            pass
        # #endregion
        return result

    # ── ANSI console rendering ─────────────────────────────────────────────

    @staticmethod
    def _render_console(
        thought: str,
        thought_num: int,
        total_thoughts: int,
        is_revision: bool,
        revises_thought: int | None,
        branch_id: str,
    ) -> None:
        BLUE   = "\033[94m"
        YELLOW = "\033[93m"
        CYAN   = "\033[96m"
        RESET  = "\033[0m"

        if branch_id:
            color = CYAN
        elif is_revision:
            color = YELLOW
        else:
            color = BLUE

        title = f"Thought {thought_num}/{total_thoughts}"
        if is_revision and revises_thought is not None:
            title += f" (Revising #{revises_thought})"
        if branch_id:
            title += f" [Branch: {branch_id}]"

        width = max(0, 73 - len(title))
        print(f"\n{color}┌─ {title} {'─' * width}{RESET}", file=sys.stderr)
        for line in thought.split("\n"):
            print(f"{color}│{RESET} {line}", file=sys.stderr)
        print(f"{color}└{'─' * 75}{RESET}\n", file=sys.stderr)

    # ── Audit / introspection ──────────────────────────────────────────────

    def get_audit_history(self) -> dict:
        return {
            "totalProcessed": self._total_processed,
            "maxBudget":      self.max_thoughts,
            "branches":       list(self.branches.keys()),
            "thoughts":       self.thought_history,
        }

    def reset(self) -> None:
        self.thought_history.clear()
        self.branches.clear()
        self._total_processed = 0
