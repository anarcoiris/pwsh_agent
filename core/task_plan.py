"""Atomic task plan tracking, state injection, and failure readaptation."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class StepStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class TaskStep:
    id: str
    label: str
    tool_hint: str
    status: StepStatus = StepStatus.PENDING
    note: str = ""


_PLACEHOLDER_PWD = re.compile(
    r"user\s*:\s*password|xmlObj\s*:\s*salt|password\s*:\s*password",
    re.I,
)


def _looks_like_placeholder_file(content: str) -> bool:
    text = (content or "").strip()
    if not text:
        return True
    if len(text) > 500:
        return False
    if _PLACEHOLDER_PWD.search(text):
        return True
    if text.lower() in ("user:password", "user:password\nxmlObj:salt"):
        return True
    return False


@dataclass
class TaskPlanTracker:
    """Track multi-step user objectives and inject roadmap state each turn."""

    prompt: str
    steps: list[TaskStep] = field(default_factory=list)
    strategy_notes: list[str] = field(default_factory=list)
    last_failure: str = ""

    def __post_init__(self) -> None:
        if not self.steps:
            self.steps = self._parse_steps_from_prompt(self.prompt)

    @staticmethod
    def _parse_steps_from_prompt(prompt: str) -> list[TaskStep]:
        lower = (prompt or "").lower()
        steps: list[TaskStep] = []

        if re.search(r"\b(read|report|plan|latest)\b", lower):
            steps.append(TaskStep(
                "read_context",
                "Read latest reports/plans/session notes",
                "read_file",
            ))

        if re.search(r"\b(pcap|pcapng|extract|xml|xmlobj|salt|password|user)\b", lower):
            steps.append(TaskStep(
                "extract_secrets",
                "Extract real user/password/xmlObj/salt from PCAP or prior analysis",
                "analyze_pcapng|read_file",
            ))

        if re.search(r"\b(pwd\.txt|save.*file|write.*file)\b", lower):
            steps.append(TaskStep(
                "write_pwd",
                "Write extracted values to pwd.txt (no placeholders)",
                "write_file",
            ))

        if re.search(r"\b(crack|hashpro|hashpro|sha-?256|crack_hash)\b", lower):
            steps.append(TaskStep(
                "crack_hash",
                "Run crack_hash with target hash, salt, prefix from extracted data",
                "crack_hash",
            ))

        if not steps and re.search(r"\b(write|create|save)\b.*\.(py|ps1|txt|md)\b", lower):
            steps.append(TaskStep(
                "deliverable",
                "Create requested deliverable on disk",
                "write_file",
            ))

        return steps

    @property
    def current_step(self) -> TaskStep | None:
        for s in self.steps:
            if s.status in (StepStatus.PENDING, StepStatus.IN_PROGRESS, StepStatus.FAILED):
                return s
        return None

    @property
    def all_done(self) -> bool:
        return bool(self.steps) and all(s.status == StepStatus.DONE for s in self.steps)

    def pending_tool_hints(self) -> list[str]:
        cur = self.current_step
        if not cur or cur.status == StepStatus.DONE:
            return []
        return [t.strip() for t in cur.tool_hint.split("|") if t.strip()]

    def register_tool(self, tool_name: str, result: Any, args: dict | None = None) -> None:
        cur = self.current_step
        if cur and cur.status == StepStatus.PENDING:
            cur.status = StepStatus.IN_PROGRESS

        if tool_name == "write_file" and isinstance(result, dict) and result.get("success"):
            content = str((args or {}).get("content", ""))
            path = str((args or {}).get("path", "")).replace("\\", "/")
            if "pwd.txt" in path.lower() and _looks_like_placeholder_file(content):
                self.last_failure = (
                    "pwd.txt is empty or uses placeholder values — extract REAL credentials "
                    "from PCAP/reports before writing."
                )
                for s in self.steps:
                    if s.id == "write_pwd":
                        s.status = StepStatus.FAILED
                        s.note = self.last_failure
                if self.steps and self.steps[0].id == "extract_secrets":
                    self.steps[0].status = StepStatus.FAILED
                return

        if tool_name == "crack_hash" and isinstance(result, dict):
            if result.get("success") is False or result.get("status") == "exhausted":
                self.last_failure = str(result.get("error") or result.get("stderr") or "crack_hash failed")
                for s in self.steps:
                    if s.id == "crack_hash":
                        s.status = StepStatus.FAILED
                        s.note = self.last_failure[:200]
                return

        if tool_name in ("read_file", "analyze_pcapng") and isinstance(result, dict) and result.get("success"):
            for s in self.steps:
                if s.status == StepStatus.FAILED and s.id in ("extract_secrets", "read_context", "write_pwd"):
                    s.status = StepStatus.PENDING
                    s.note = ""
            self.last_failure = ""

        for s in self.steps:
            if s.status in (StepStatus.DONE, StepStatus.SKIPPED):
                continue
            hints = [h.strip() for h in s.tool_hint.split("|")]
            if tool_name in hints:
                if isinstance(result, dict) and result.get("success") is False:
                    s.status = StepStatus.FAILED
                    s.note = str(result.get("error", "tool failed"))[:200]
                    self.last_failure = s.note
                else:
                    s.status = StepStatus.DONE
                break

    def needs_readaptation(self) -> bool:
        return any(s.status == StepStatus.FAILED for s in self.steps)

    def record_strategy(self, note: str) -> None:
        line = note.strip()
        if line and (not self.strategy_notes or self.strategy_notes[-1] != line):
            self.strategy_notes.append(line)

    def append_scratchpad(self, session_id: str, task_id: str, line: str) -> None:
        from core.session_paths import scratchpad_file
        fp = scratchpad_file(session_id, task_id)
        fp.parent.mkdir(parents=True, exist_ok=True)
        ts = __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = f"[{ts}] {line.strip()}\n"
        if not fp.is_file():
            fp.write_text(f"# Scratchpad — {task_id}\n\n{entry}", encoding="utf-8")
        else:
            with open(fp, "a", encoding="utf-8") as f:
                f.write(entry)

    def status_block(self) -> str:
        if not self.steps:
            return ""
        lines = ["[TASK ROADMAP — current state]"]
        for s in self.steps:
            mark = {
                StepStatus.PENDING: "[ ]",
                StepStatus.IN_PROGRESS: "[~]",
                StepStatus.DONE: "[x]",
                StepStatus.FAILED: "[!]",
                StepStatus.SKIPPED: "[-]",
            }[s.status]
            line = f"{mark} {s.label} (tool: {s.tool_hint})"
            if s.note:
                line += f" — {s.note[:120]}"
            lines.append(line)
        cur = self.current_step
        if cur:
            lines.append(f"CURRENT: {cur.label} → use `{cur.tool_hint.split('|')[0]}` next.")
        if self.strategy_notes:
            lines.append("STRATEGY NOTES:")
            lines.extend(f"  - {n}" for n in self.strategy_notes[-3:])
        return "\n".join(lines)

    def readaptation_directive(self) -> str:
        parts = [
            "[SYSTEM — PLAN READAPTATION REQUIRED]",
            "A roadmap step failed or produced invalid output. Do NOT repeat the same action.",
            self.status_block(),
        ]
        if self.last_failure:
            parts.append(f"LAST FAILURE: {self.last_failure}")
        parts.append(
            "Readapt now:\n"
            "1) append_note a one-line strategy change to the session plan file.\n"
            "2) If analyze_pcapng already ran, parse credential fields from that tool result "
            "(http_forms, potential_plaintext_credentials) — do NOT guess report paths.\n"
            "3) find_file report_*.md or read_file .pulse/pcap_logs/verbose_*.txt if values still missing.\n"
            "4) write_file pwd.txt with REAL extracted values, then crack_hash."
        )
        return "\n".join(p for p in parts if p)

    def may_complete_turn(self, tools_executed: list[str], step_index: int, min_steps: int = 2) -> bool:
        if not self.steps:
            return step_index >= min_steps
        if self.needs_readaptation():
            return False
        if not self.all_done:
            return False
        return step_index >= min_steps


def load_session_context_snippets(
    app_root: Path,
    workspace_root: Path,
    session_id: str | None = None,
    max_chars: int = 1500,
) -> str:
    """Load current session plan/status plus discoverable reports and PCAP logs."""
    from core.session_paths import (
        load_active_session_id,
        plan_file,
        status_file,
        session_workspace_dir,
        list_prior_artifact_snippets,
    )

    sid = session_id or load_active_session_id()
    parts: list[str] = []

    candidates: list[Path] = [
        plan_file(sid),
        status_file(sid),
        session_workspace_dir(sid) / "pwd.txt",
        workspace_root / "pwd.txt",
        app_root / "workspace" / "pwd.txt",
        workspace_root / "plan.md",
        app_root / "workspace" / "plan.md",
    ]

    for root in (app_root, workspace_root):
        for sub in ("output", "reports", "workspace/reports"):
            d = root / sub
            if d.is_dir():
                candidates.extend(sorted(d.glob("report_*.md"), reverse=True)[:2])

    pcap_dir = app_root / ".pulse" / "pcap_logs"
    if pcap_dir.is_dir():
        logs = sorted(pcap_dir.glob("verbose_*.txt"), reverse=True)
        candidates.extend(logs[:1])

    for path in candidates:
        if not path.exists() or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        try:
            rel = path.resolve().relative_to(app_root.resolve()).as_posix()
        except ValueError:
            rel = path.name
        parts.append(f"--- {rel} (head) ---\n{text[:400]}")
        if sum(len(p) for p in parts) > max_chars:
            break

    prior = list_prior_artifact_snippets(max_sessions=2, max_chars=400)
    if prior and sum(len(p) for p in parts) < max_chars:
        parts.append(prior)

    if not parts:
        return ""
    return "[SESSION CONTEXT — artifacts on disk]\n" + "\n\n".join(parts)[:max_chars]
