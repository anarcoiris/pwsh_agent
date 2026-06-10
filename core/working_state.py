"""Canonical CURRENT STATE block and volatile working memory.

Phase 2 of the context optimization plan. The agent sends the LLM a single,
fixed-order, hard-budgeted CURRENT STATE injection instead of several
overlapping blocks (session snippet + plan status + drafts + nudges). The raw
message history is kept on disk as on-demand backup, not re-sent in full.

Two distinct stores feed CURRENT STATE:
  - working_memory: VOLATILE reasoning scratch (last_observation, hypothesis,
    next_action, last_failure). Overwritten freely; tiny and fixed-size so it
    never becomes a parallel history.
  - facts (core/facts_store): PERSISTENT structured facts/entities/artifacts,
    no narrative.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Budget: a small 7B benefits from a tight, predictable state block. The token
# figure is the contract; the char figure is the cheap fallback when no
# tokenizer is available (~4 chars/token heuristic, matching core/context.py).
MAX_CURRENT_STATE_TOKENS = 1500
MAX_CURRENT_STATE_CHARS = MAX_CURRENT_STATE_TOKENS * 4

_HEADER = "### CURRENT STATE ###"
_FOOTER = "#" * len(_HEADER)


@dataclass
class WorkingMemory:
    """Volatile reasoning scratch with a fixed, tiny footprint."""

    last_observation: str = ""
    current_hypothesis: str = ""
    next_action: str = ""
    last_failure: str = ""

    def update(
        self,
        *,
        observation: str | None = None,
        hypothesis: str | None = None,
        next_action: str | None = None,
        failure: str | None = None,
    ) -> None:
        if observation is not None:
            self.last_observation = observation.strip()[:400]
        if hypothesis is not None:
            self.current_hypothesis = hypothesis.strip()[:300]
        if next_action is not None:
            self.next_action = next_action.strip()[:300]
        if failure is not None:
            self.last_failure = failure.strip()[:300]

    def clear_strategy(self) -> None:
        """Drop hypothesis/next_action when strategy changes (TTL semantics)."""
        self.current_hypothesis = ""
        self.next_action = ""

    def is_empty(self) -> bool:
        return not any(
            (self.last_observation, self.current_hypothesis, self.next_action, self.last_failure)
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "last_observation": self.last_observation,
            "current_hypothesis": self.current_hypothesis,
            "next_action": self.next_action,
            "last_failure": self.last_failure,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "WorkingMemory":
        data = data or {}
        return cls(
            last_observation=str(data.get("last_observation", "")),
            current_hypothesis=str(data.get("current_hypothesis", "")),
            next_action=str(data.get("next_action", "")),
            last_failure=str(data.get("last_failure", "")),
        )


def _working_memory_path(session_id: str) -> Path:
    from core.session_paths import session_state_dir

    return session_state_dir(session_id) / "working_memory.json"


def load_working_memory(session_id: str) -> WorkingMemory:
    from core.session_paths import session_state_dir
    db_path = session_state_dir(session_id) / "session.db"
    
    data = None
    if db_path.is_file():
        from core.session_db import SessionDB
        db = SessionDB(session_id)
        try:
            data = db.get_state("working_memory")
        finally:
            db.close()
    else:
        path = _working_memory_path(session_id)
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
    return WorkingMemory.from_dict(data)


def save_working_memory(session_id: str, wm: WorkingMemory) -> Path | None:
    if wm.is_empty():
        return None
    from core.session_paths import session_state_dir
    db_path = session_state_dir(session_id) / "session.db"
    if db_path.is_file():
        from core.session_db import SessionDB
        db = SessionDB(session_id)
        try:
            db.set_state("working_memory", wm.to_dict())
        finally:
            db.close()
        return _working_memory_path(session_id)

    path = _working_memory_path(session_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(wm.to_dict(), indent=2), encoding="utf-8")
        return path
    except OSError:
        return None


def _facts_to_lines(facts_block: str) -> str:
    """Strip the standalone [SESSION FACTS] header so it nests cleanly."""
    text = (facts_block or "").strip()
    if text.startswith("[SESSION FACTS]"):
        text = text[len("[SESSION FACTS]"):].strip()
    return text


def current_state_file(session_id: str) -> Path:
    from core.session_paths import session_state_dir

    return session_state_dir(session_id) / "CURRENT_STATE.md"


def save_current_state(session_id: str, content: str) -> Path | None:
    """Persist CURRENT STATE block to the session directory (audit/replay only).

    The LLM never reads this file back — each turn rebuilds state via
    build_current_state() from working_memory.json, plan_state.json, and RAM.
    """
    text = (content or "").strip()
    if not text:
        return None
    from core.session_paths import session_state_dir
    db_path = session_state_dir(session_id) / "session.db"
    if db_path.is_file():
        from core.session_db import SessionDB
        db = SessionDB(session_id)
        try:
            db.set_state("current_state_md", text)
        finally:
            db.close()

    path = current_state_file(session_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
        return path
    except OSError:
        return None


def _format_declared_intent(declared: dict[str, Any] | None) -> str:
    if not declared:
        return ""
    lines: list[str] = []
    if declared.get("domain"):
        lines.append(f"domain={declared['domain']}")
    if declared.get("summary"):
        lines.append(f"goal={declared['summary']}")
    objs = declared.get("objectives") or []
    if objs:
        lines.append("objectives=" + "; ".join(str(o) for o in objs[:6]))
    targets = declared.get("targets") or []
    if targets:
        lines.append("targets=" + ", ".join(str(t) for t in targets[:6]))
    criteria = declared.get("success_criteria") or []
    if criteria:
        lines.append("done_when=" + "; ".join(str(c) for c in criteria[:4]))
    constraints = declared.get("constraints") or []
    if constraints:
        lines.append("constraints=" + "; ".join(str(c) for c in constraints[:4]))
    suggested = declared.get("suggested_agent")
    if suggested:
        lines.append(f"suggested_delegate={suggested}")
    return "\n".join(lines)


def build_current_state(
    *,
    mission: str = "",
    plan: dict[str, Any] | None = None,
    working_memory: WorkingMemory | None = None,
    last_tool_result: str = "",
    draft: str = "",
    facts_block: str = "",
    artifact_refs: list[str] | None = None,
    readaptation: str = "",
    active_agent: str = "",
    handoff_brief: str = "",
    return_to_lead_when: str = "",
    declared_intent: dict[str, Any] | None = None,
    handoff_complete: bool = False,
    manager_plan: list[str] | None = None,
    current_task: dict[str, str] | None = None,
    prior_handoff: str = "",
    max_chars: int = MAX_CURRENT_STATE_CHARS,
) -> str:
    """Compose the single canonical CURRENT STATE block with a hard budget.

    Fixed section order (contract, so the small model always sees the same
    priorities): ACTIVE AGENT -> HANDOFF -> MISSION -> DECLARED INTENT ->
    NEXT ACTION -> LAST FAILURE -> READAPTATION -> LAST TOOL RESULT -> DRAFT ->
    WORKING MEMORY -> COMPACT FACTS -> ARTIFACT REFS.

    active_agent and handoff_brief always win truncation (high priority).
    """
    plan = plan or {}
    wm = working_memory or WorkingMemory()

    next_action = (plan.get("next_action") or wm.next_action or "").strip()
    last_failure = (plan.get("last_failure") or wm.last_failure or "").strip()

    # (header, body) sections in fixed priority order.
    sections: list[tuple[str, str]] = []

    agent = (active_agent or "").strip()
    if agent:
        agent_body = f"active_agent={agent}"
        if handoff_complete:
            agent_body += "\n[HANDOFF COMPLETE — review and delegate or conclude]"
        sections.append(("ACTIVE AGENT", agent_body))

    if prior_handoff.strip():
        sections.append(("PRIOR HANDOFF", prior_handoff.strip()[:700]))

    if manager_plan:
        sections.append(("MANAGER PLAN", "\n".join(manager_plan[:12])))

    ct = current_task or {}
    if ct.get("id"):
        ct_lines = [
            f"task={ct.get('id', '')}",
            f"agent={ct.get('assigned_agent', '')}",
            f"label={ct.get('label', '')}",
        ]
        if ct.get("brief"):
            ct_lines.append(f"brief={ct['brief']}")
        if ct.get("success_criteria"):
            ct_lines.append(f"done_when={ct['success_criteria']}")
        sections.append(("CURRENT TASK", "\n".join(ct_lines)))

    if handoff_brief.strip():
        sections.append(("HANDOFF", handoff_brief.strip()[:800]))

    if return_to_lead_when.strip():
        sections.append(("RETURN TO LEAD", return_to_lead_when.strip()[:400]))

    if mission.strip():
        sections.append(("MISSION", mission.strip()[:600]))

    intent_lines = _format_declared_intent(declared_intent)
    if intent_lines:
        sections.append(("DECLARED INTENT", intent_lines))

    na_lines: list[str] = []
    if next_action:
        na_lines.append(next_action)
    if plan.get("phase"):
        na_lines.append(f"phase={plan['phase']}")
    if plan.get("done_steps"):
        na_lines.append(f"done={','.join(plan['done_steps'])}")
    if plan.get("strategy"):
        na_lines.append(f"strategy={plan['strategy']}")
    if na_lines:
        sections.append(("NEXT ACTION", "\n".join(na_lines)))

    if last_failure:
        sections.append(("LAST FAILURE", last_failure))

    if readaptation.strip():
        sections.append(("READAPTATION", readaptation.strip()[:1200]))

    if last_tool_result.strip():
        sections.append(("LAST TOOL RESULT", last_tool_result.strip()))

    if draft.strip():
        sections.append(("DRAFT", draft.strip()))

    wm_lines: list[str] = []
    if wm.last_observation:
        wm_lines.append(f"observation={wm.last_observation}")
    if wm.current_hypothesis:
        wm_lines.append(f"hypothesis={wm.current_hypothesis}")
    if wm_lines:
        sections.append(("WORKING MEMORY", "\n".join(wm_lines)))

    facts_lines = _facts_to_lines(facts_block)
    if facts_lines:
        sections.append(("COMPACT FACTS", facts_lines))

    if artifact_refs:
        sections.append(("ARTIFACT REFS", "\n".join(f"- {r}" for r in artifact_refs if r)))

    if not sections:
        return ""

    # Budget for the body (reserve room for header/footer wrapper).
    wrapper_len = len(_HEADER) + len(_FOOTER) + 2
    body_budget = max(0, max_chars - wrapper_len)

    rendered: list[str] = []
    used = 0
    for header, body in sections:
        block = f"[{header}]\n{body}"
        sep = 2 if rendered else 0  # "\n\n" between blocks
        if used + sep + len(block) <= body_budget:
            rendered.append(block)
            used += sep + len(block)
            continue
        # Not enough room for the full block: truncate to remaining budget if a
        # meaningful slice fits, then stop (lower-priority sections are dropped).
        remaining = body_budget - used - sep
        if remaining > len(header) + 24:
            head = f"[{header}]\n"
            slice_len = remaining - len(head) - 3
            rendered.append(head + body[:max(0, slice_len)] + "...")
        break

    if not rendered:
        return ""
    return f"{_HEADER}\n" + "\n\n".join(rendered) + f"\n{_FOOTER}"
