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
    PARTIAL = "partial"  # bounded terminal (e.g. search space exhausted)


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


def _looks_like_extraction_draft(content: str) -> bool:
    """True when content is a PCAP extraction stub, not final cracked results."""
    text = (content or "").strip()
    if not text:
        return True
    lower = text.lower()
    if "from analyze_pcapng http_forms" in lower:
        return True
    if "# full decode: read_file" in lower:
        return True
    if "plaintext=" in lower or "crack_status=" in lower:
        return False
    if re.search(r"\b[a-f0-9]{64}\b", text) and "salt=" in lower:
        return False
    return False


def _looks_like_placeholder_file(content: str) -> bool:
    text = (content or "").strip()
    if not text:
        return True
    if _looks_like_extraction_draft(text):
        return True
    if len(text) > 500:
        return False
    if _PLACEHOLDER_PWD.search(text):
        return True
    if text.lower() in ("user:password", "user:password\nxmlObj:salt"):
        return True
    lower = text.lower()
    has_user_pass = any(
        k in lower
        for k in ("username", "password", "user:", "password:", "user=", "password=")
    )
    has_xml_or_salt = "xmlobj" in lower or re.search(r"\bsalt\b", lower)
    if has_xml_or_salt and not has_user_pass:
        return True
    return False


@dataclass
class TaskPlanTracker:
    """Track multi-step user objectives and inject roadmap state each turn."""

    prompt: str
    steps: list[TaskStep] = field(default_factory=list)
    strategy_notes: list[str] = field(default_factory=list)
    last_failure: str = ""
    evidence_seen: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        if not self.steps:
            self.steps = self._parse_steps_from_prompt(self.prompt)

    @staticmethod
    def _parse_steps_from_prompt(prompt: str) -> list[TaskStep]:
        lower = (prompt or "").lower()
        steps: list[TaskStep] = []

        # Domain-aware planning (Phase 3). For domains the legacy credential
        # keyword logic mishandles (notably web_auth), build appropriate steps
        # and return early. Forensic prompts classify as hash/pcap and fall
        # through to the original logic below — unchanged.
        try:
            from core.intent_spec import build_fallback_spec
            domain = build_fallback_spec(prompt).domain
        except Exception:
            domain = ""

        if domain == "web_auth":
            if re.search(r"\.(txt|md|json|cfg|conf)\b", lower) or re.search(r"\bfile\b", lower):
                steps.append(TaskStep(
                    "read_credentials",
                    "Read the referenced credential/password file",
                    "read_file",
                ))
            steps.append(TaskStep(
                "attempt_login",
                "Attempt authentication against the target endpoint",
                "try_http_login",
            ))
            return steps

        if re.search(r"\b(read|report|plan|latest)\b", lower):
            steps.append(TaskStep(
                "read_context",
                "Read latest reports/plans/session notes",
                "read_file",
            ))

        if domain in ("hash", "pcap") and re.search(
            r"\b(pcap|pcapng|extract|xml|xmlobj|salt|password|user)\b", lower
        ):
            steps.append(TaskStep(
                "extract_secrets",
                "Extract real user/password/xmlObj/salt from PCAP or prior analysis",
                "analyze_pcapng|read_file",
            ))

        if re.search(r"\b(pwd\.txt|login_forms\.txt|save.*file|write.*file|file\s+named)\b", lower):
            from core.task_intent import TaskIntentExtractor
            dels = TaskIntentExtractor._extract_deliverables(prompt)
            target = next((d for d in dels if d.endswith(".txt")), "pwd.txt")
            steps.append(TaskStep(
                "write_deliverable",
                f"Write extracted values to {target} (no placeholders)",
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
        evidence: set[str] = set()

        if tool_name == "write_file" and isinstance(result, dict) and result.get("success"):
            content = str((args or {}).get("content", ""))
            path = str((args or {}).get("path", "")).replace("\\", "/")
            from core.task_intent import (
                path_matches_deliverable,
                TaskIntentExtractor,
                _is_credential_deliverable,
            )

            expected = TaskIntentExtractor._extract_deliverables(self.prompt)
            expected_txt = next((d for d in expected if d.endswith(".txt")), "")
            if expected_txt and not path_matches_deliverable(path, [expected_txt]):
                self.last_failure = (
                    f"Wrote '{path}' but required deliverable is '{expected_txt}'."
                )
                for s in self.steps:
                    if s.id == "write_deliverable":
                        s.status = StepStatus.FAILED
                        s.note = self.last_failure
                return

            if _is_credential_deliverable(path) and _looks_like_placeholder_file(content):
                self.last_failure = (
                    f"{path} is empty or uses placeholder/incomplete values — extract REAL "
                    "user/password/xmlObj/salt from PCAP/reports before writing."
                )
                for s in self.steps:
                    if s.id == "write_deliverable":
                        s.status = StepStatus.FAILED
                        s.note = self.last_failure
                if self.steps and self.steps[0].id == "extract_secrets":
                    self.steps[0].status = StepStatus.FAILED
                return

        if tool_name == "try_http_login":
            # Making the attempt satisfies the step regardless of the verdict
            # (accepted / rejected / unreachable are all reportable terminals).
            for s in self.steps:
                if s.id == "attempt_login":
                    s.status = StepStatus.DONE
                    if isinstance(result, dict):
                        s.note = str(result.get("verdict") or result.get("error") or "login attempt made")[:120]
                    else:
                        s.note = "login attempt made"
            return

        if tool_name == "crack_hash" and isinstance(result, dict):
            if result.get("status") == "exhausted":
                # Search space fully explored — bounded terminal, not a failure
                for s in self.steps:
                    if s.id == "crack_hash":
                        s.status = StepStatus.PARTIAL
                        s.note = "Search space exhausted without match"
                return
            if result.get("success") is False:
                # Genuine tool error (bad args, launcher missing)
                self.last_failure = str(result.get("error") or result.get("stderr") or "crack_hash failed")
                for s in self.steps:
                    if s.id == "crack_hash":
                        s.status = StepStatus.FAILED
                        s.note = self.last_failure[:200]
                return

        if tool_name in ("read_file", "analyze_pcapng") and isinstance(result, dict) and result.get("success"):
            evidence = self._extract_evidence_markers(tool_name, result)
            if evidence:
                self.evidence_seen.update(evidence)
                for s in self.steps:
                    if s.status == StepStatus.FAILED and s.id in ("extract_secrets", "read_context", "write_deliverable"):
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
                    if (
                        s.id == "extract_secrets"
                        and tool_name in ("read_file", "analyze_pcapng")
                        and "cred_text" not in evidence
                    ):
                        s.status = StepStatus.FAILED
                        s.note = "No credential/salt evidence in read/analyze output"
                        self.last_failure = s.note
                    else:
                        s.status = StepStatus.DONE
                break

    def needs_readaptation(self) -> bool:
        # PARTIAL is intentionally excluded — it is a valid terminal outcome.
        return any(s.status == StepStatus.FAILED for s in self.steps)

    @staticmethod
    def _extract_evidence_markers(tool_name: str, result: dict[str, Any]) -> set[str]:
        markers: set[str] = set()
        if tool_name == "read_file":
            text = str(result.get("content", "")).lower()
            if any(k in text for k in ("xmlobj", "password", "username", "login_entry", "_sessiontoken")):
                markers.add("cred_text")
            if re.search(r"\b[a-f0-9]{64}\b", text):
                markers.add("sha256")
            return markers

        if tool_name == "analyze_pcapng":
            analysis = result.get("analysis") if isinstance(result.get("analysis"), dict) else {}
            blob = "\n".join(
                str(analysis.get(k, ""))
                for k in ("key_fields", "potential_plaintext_credentials", "http_forms", "packet_summary")
            ).lower()
            if any(k in blob for k in ("xmlobj", "password", "username", "login_entry")):
                markers.add("cred_text")
            if re.search(r"\b[a-f0-9]{64}\b", blob):
                markers.add("sha256")
            if analysis.get("verbose_log_file"):
                markers.add("verbose_log")
            return markers

        return markers

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
                StepStatus.PARTIAL: "[≈]",
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

    def compact(self) -> dict[str, Any]:
        """Compact plan state for the canonical CURRENT STATE injection.

        Returns only the decision-relevant fields; the full checklist
        (status_block) stays on disk via save_plan_state.
        """
        cur = self.current_step
        phase = ""
        next_action = ""
        if cur:
            phase = cur.id
            first_tool = cur.tool_hint.split("|")[0].strip()
            next_action = f"{cur.label} (use `{first_tool}`)" if first_tool else cur.label
        return {
            "goal": (self.prompt or "").strip()[:160],
            "phase": phase,
            "next_action": next_action,
            "done_steps": [s.id for s in self.steps if s.status == StepStatus.DONE],
            "last_failure": (self.last_failure or "")[:200],
            "strategy": self.strategy_notes[-1] if self.strategy_notes else "",
        }

    def readaptation_directive(self) -> str:
        parts = [
            "[SYSTEM — PLAN READAPTATION REQUIRED]",
            "A roadmap step failed or produced invalid output. Do NOT repeat the same action.",
            self.status_block(),
        ]
        if self.evidence_seen:
            parts.append(f"EVIDENCE SEEN: {', '.join(sorted(self.evidence_seen))}")
        if self.last_failure:
            parts.append(f"LAST FAILURE: {self.last_failure}")
        parts.append(
            "Readapt now:\n"
            "1) append_note a one-line strategy change to the session plan file.\n"
            "2) If analyze_pcapng already ran, parse credential fields from that tool result "
            "(http_forms, potential_plaintext_credentials) — do NOT guess report paths.\n"
            "3) find_file('report_*.md') then read_file(path=<recommended>) — or read_file('report_*.md') directly.\n"
            "4) find_and_grep(pattern='xml|Password|Username|616a6178|xmlObj', path_glob='.pulse/pcap_logs/verbose_*.txt', case_insensitive=true, max_files=10) "
            "— or grep_file with pattern='xml' if a single log is known — or use pcap.verbose_log_file from SESSION FACTS.\n"
            "5) write_file the user deliverable with REAL values from http_forms/key_fields, then crack_hash."
        )
        return "\n".join(p for p in parts if p)

    def may_complete_turn(self, tools_executed: list[str], step_index: int, min_steps: int = 2) -> bool:
        if not self.steps:
            return step_index >= min_steps
        if self.needs_readaptation():
            return False
        terminal = {StepStatus.DONE, StepStatus.PARTIAL, StepStatus.SKIPPED}
        if not all(s.status in terminal for s in self.steps):
            return False
        return step_index >= min_steps


def load_session_context_snippets(
    app_root_path: Path,
    workspace_root_path: Path,
    session_id: str | None = None,
    max_chars: int = 1500,
) -> str:
    """Load session-scoped notes, reports, and PCAP logs (excludes legacy workspace/plan.md)."""
    from core.path_catalog import session_context_paths, rel_path
    from core.session_paths import load_active_session_id, list_prior_artifact_snippets

    sid = session_id or load_active_session_id()
    parts: list[str] = []

    for label, path in session_context_paths(sid):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        head = 350 if label.startswith("session_") else 400
        parts.append(f"--- {label}: {rel_path(path)} ---\n{text[:head]}")
        if sum(len(p) for p in parts) > max_chars:
            break

    prior = list_prior_artifact_snippets(max_sessions=2, max_chars=400)
    if prior and sum(len(p) for p in parts) < max_chars:
        parts.append(prior)

    if not parts:
        return ""
    return "[SESSION CONTEXT — artifacts on disk]\n" + "\n\n".join(parts)[:max_chars]


def _tracker_to_dict(tracker: TaskPlanTracker) -> dict[str, Any]:
    return {
        "prompt": tracker.prompt,
        "steps": [
            {
                "id": s.id,
                "label": s.label,
                "tool_hint": s.tool_hint,
                "status": s.status.value,
                "note": s.note,
            }
            for s in tracker.steps
        ],
        "strategy_notes": list(tracker.strategy_notes),
        "last_failure": tracker.last_failure,
        "evidence_seen": sorted(tracker.evidence_seen),
    }


def _tracker_from_dict(data: dict[str, Any]) -> TaskPlanTracker:
    tracker = TaskPlanTracker(data.get("prompt", ""))
    tracker.steps = []
    for raw in data.get("steps", []):
        if not isinstance(raw, dict):
            continue
        status_raw = str(raw.get("status", StepStatus.PENDING.value))
        try:
            status = StepStatus(status_raw)
        except ValueError:
            status = StepStatus.PENDING
        tracker.steps.append(TaskStep(
            id=str(raw.get("id", "")),
            label=str(raw.get("label", "")),
            tool_hint=str(raw.get("tool_hint", "")),
            status=status,
            note=str(raw.get("note", "")),
        ))
    tracker.strategy_notes = list(data.get("strategy_notes", []))
    tracker.last_failure = str(data.get("last_failure", ""))
    tracker.evidence_seen = set(data.get("evidence_seen", []))
    return tracker


def save_plan_state(session_id: str, tracker: TaskPlanTracker) -> Path | None:
    """Persist in-progress roadmap across turns."""
    if not tracker.steps:
        return None
    from core.session_paths import plan_state_file

    path = plan_state_file(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_tracker_to_dict(tracker), indent=2), encoding="utf-8")
    return path


_GENERIC_DOMAINS = {"", "general", "conversation", "mixed"}


def load_plan_state(
    session_id: str, current_message: str | None = None
) -> TaskPlanTracker | None:
    """Load persisted roadmap if present and not fully complete.

    When ``current_message`` is provided, discard (and clear from disk) any
    rehydrated plan whose original intent domain differs from the new message's
    domain. This prevents a stale credential/PCAP roadmap from hijacking an
    unrelated follow-up turn in a resumed session.
    """
    from core.session_paths import plan_state_file

    path = plan_state_file(session_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        tracker = _tracker_from_dict(data)
        if tracker.all_done:
            return None
        if current_message:
            try:
                from core.intent_spec import build_fallback_spec

                prev = build_fallback_spec(tracker.prompt).domain
                new = build_fallback_spec(current_message).domain
            except Exception:
                prev = new = ""
            if (
                prev not in _GENERIC_DOMAINS
                and new not in _GENERIC_DOMAINS
                and prev != new
            ):
                clear_plan_state(session_id)
                return None
        return tracker
    except (OSError, json.JSONDecodeError):
        return None


def clear_plan_state(session_id: str) -> None:
    from core.session_paths import plan_state_file

    path = plan_state_file(session_id)
    try:
        if path.is_file():
            path.unlink()
    except OSError:
        pass
