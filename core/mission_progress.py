"""Mission progress tracking for objective-aware completion and anti-stall."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


_OBJECTIVE_RE = re.compile(
    r"(retrieve|login|password|xmlobj|salt|don't stop|do not stop|credential)",
    re.I,
)


@dataclass
class MissionProgressTracker:
    prompt: str
    objective_keywords: set[str] = field(default_factory=set)
    tools_executed: list[str] = field(default_factory=list)
    substantive_tools: int = 0
    non_substantive_streak: int = 0
    append_note_count: int = 0
    has_analyze_success: bool = False
    has_read_secret_hit: bool = False
    has_finding_record: bool = False
    extracted_secrets: bool = False
    last_verbose_log_file: str | None = None
    last_filter_expression: str | None = None
    last_analyze_ok: bool = False

    def __post_init__(self) -> None:
        lower = (self.prompt or "").lower()
        self.objective_keywords = {
            m.group(0).lower()
            for m in _OBJECTIVE_RE.finditer(lower)
        }

    @property
    def retrieval_mission(self) -> bool:
        return bool(self.objective_keywords)

    @staticmethod
    def _is_substantive(tool_name: str) -> bool:
        return tool_name in {
            "find_file",
            "analyze_pcapng",
            "read_file",
            "host_exec",
            "run_script",
            "capture_packets",
            "crack_hash",
            "finding_create",
            "dns_lookup",
            "ping_sweep",
            "port_scan",
            "system_info",
            "http_headers_check",
            "ssl_analysis",
            "cve_lookup",
            "encode_decode",
            "hash_identify",
            "report_generate",
            "list_network_interfaces",
        }

    def register(
        self,
        tool_name: str,
        result: Any,
        did_execute: bool,
        blocked_or_duplicate: bool = False,
    ) -> None:
        if not did_execute:
            if blocked_or_duplicate:
                self.non_substantive_streak += 1
            return

        self.tools_executed.append(tool_name)
        if self._is_substantive(tool_name):
            self.substantive_tools += 1
            self.non_substantive_streak = 0
        else:
            self.non_substantive_streak += 1

        if tool_name == "append_note":
            self.append_note_count += 1

        if not isinstance(result, dict):
            return

        if tool_name == "analyze_pcapng" and result.get("success"):
            self.has_analyze_success = True
            self.last_analyze_ok = True
            analysis = result.get("analysis", {}) or {}
            self.extracted_secrets = self.extracted_secrets or bool(
                analysis.get("extracted_secrets")
            )
            log_path = analysis.get("verbose_log_file")
            if log_path:
                self.last_verbose_log_file = str(log_path)
            self.last_filter_expression = str(analysis.get("filter_expression", "")) or self.last_filter_expression
        elif tool_name == "analyze_pcapng":
            self.last_analyze_ok = False

        if tool_name == "read_file" and result.get("success"):
            content = str(result.get("content", ""))
            if re.search(r"(login|password|xmlobj)", content, re.I):
                self.has_read_secret_hit = True

        if tool_name == "finding_create" and result.get("success"):
            self.has_finding_record = True

    def objective_satisfied(self) -> bool:
        if not self.retrieval_mission:
            # Generic missions must show substantive progress, not trivial tool spam.
            return self.substantive_tools >= 2
        return self.has_analyze_success and (
            self.extracted_secrets
            or self.has_read_secret_hit
            or self.has_finding_record
        )

    def needs_stall_recovery(self) -> bool:
        if self.non_substantive_streak >= 3:
            return True
        if self.append_note_count >= 2 and not self.objective_satisfied():
            return True
        return False

    def stall_directive(self) -> str:
        base = (
            "[SYSTEM DIRECTIVE] Mission objective not satisfied yet. "
            "Do NOT call append_note or repeat find_file now. "
        )
        if self.last_verbose_log_file:
            return (
                base
                + f"Read the saved log in chunks: read_file(path=\"{self.last_verbose_log_file}\", "
                  "line_start=1, line_count=80). Continue by next_line_start until credentials are found."
            )
        return (
            base
            + "Call analyze_pcapng with a narrower filter and verbose=true, then inspect key_fields. "
              "If a verbose_log_file is returned, read it in chunks with read_file."
        )
