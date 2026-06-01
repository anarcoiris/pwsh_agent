"""Tests for Phase 2 history window, nudge collapse, plan compact, and the
single CURRENT STATE injection (no duplicate state blocks)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.context import AgentContextManager, _collapse_stale_nudges
from core.context_router import ContextRouter
from core.task_plan import TaskPlanTracker


def _mgr(name: str) -> AgentContextManager:
    mgr = AgentContextManager(
        session_id=name,
        max_total_context=200,
        max_context_chars=100000,
        max_tool_result_chars=22000,
        state_path=str(Path(__file__).parent / f"_tmp_{name}.json"),
    )
    mgr.clear_history()
    return mgr


def test_messages_for_llm_windows_recent_turns():
    mgr = _mgr("win")
    base = [{"role": "system", "content": "sys"}, {"role": "user", "content": "real mission"}]
    turns = []
    for i in range(20):
        turns.append({"role": "assistant", "content": f"a{i}", "tool_calls": [{"function": {"name": "host_exec"}}]})
        turns.append({"role": "tool", "name": "host_exec", "content": f"r{i}"})
    mgr.messages = base + turns
    windowed = mgr.messages_for_llm(max_turns=4)
    # Full log untouched.
    assert len(mgr.messages) == len(base + turns)
    # Window is strictly smaller and keeps the pinned prefix.
    assert len(windowed) < len(mgr.messages)
    assert windowed[0]["role"] == "system"
    assert windowed[1]["content"] == "real mission"
    # The most recent tool result is present; an early one is not.
    flat = " ".join(str(m.get("content", "")) for m in windowed)
    assert "r19" in flat
    assert "r0 " not in flat
    mgr.state_path.unlink(missing_ok=True)


def test_messages_for_llm_no_window_returns_all():
    mgr = _mgr("nowin")
    mgr.messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    assert mgr.messages_for_llm(0) is mgr.messages
    mgr.state_path.unlink(missing_ok=True)


def test_collapse_stale_nudges_keeps_recent_and_anchor():
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "the real user mission about pcap analysis"},
        {"role": "user", "content": "[SYSTEM] nudge 1"},
        {"role": "assistant", "content": "thinking"},
        {"role": "user", "content": "[SYSTEM] nudge 2"},
        {"role": "user", "content": "[SYSTEM] nudge 3"},
        {"role": "user", "content": "[SYSTEM] nudge 4"},
    ]
    out = _collapse_stale_nudges(msgs, keep_recent=2)
    nudges = [m for m in out if str(m.get("content", "")).startswith("[SYSTEM]")]
    assert len(nudges) == 2
    assert nudges[0]["content"] == "[SYSTEM] nudge 3"
    assert nudges[1]["content"] == "[SYSTEM] nudge 4"
    # The real mission (anchor) is never dropped.
    assert any("real user mission" in str(m.get("content", "")) for m in out)


def test_trim_collapses_nudges():
    mgr = _mgr("collapse")
    mgr.messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "real mission text here"}]
    for i in range(6):
        mgr.messages.append({"role": "user", "content": f"[SYSTEM] directive {i}"})
    mgr.trim_context()
    nudges = [m for m in mgr.messages if str(m.get("content", "")).startswith("[SYSTEM]")]
    assert len(nudges) <= 2
    mgr.state_path.unlink(missing_ok=True)


def test_plan_compact_shape():
    tracker = TaskPlanTracker("decode last_capture.pcapng for credentials then crack hash")
    assert tracker.steps  # builder derived steps
    c = tracker.compact()
    assert set(["goal", "phase", "next_action", "done_steps", "last_failure", "strategy"]).issubset(c)
    assert isinstance(c["done_steps"], list)
    assert c["goal"]


def test_current_state_injection_is_single_block():
    messages = [{"role": "user", "content": "analyze x.pcapng"}]
    cs = "### CURRENT STATE ###\n[MISSION]\nanalyze x.pcapng\n####################"
    injections = ContextRouter.build_injections(
        messages,
        anchor_query="analyze x.pcapng",
        current_state=cs,
        session_snippet="should be ignored",
        plan_block="should be ignored too",
    )
    contents = [i["content"] for i in injections]
    assert any(c == cs for c in contents)
    # The legacy separate blocks must NOT appear when current_state is provided.
    assert not any("### SESSION CONTEXT ###" in c for c in contents)
    assert not any("### TASK PLAN STATUS ###" in c for c in contents)
    assert not any("should be ignored" in c for c in contents)


if __name__ == "__main__":
    test_messages_for_llm_windows_recent_turns()
    test_messages_for_llm_no_window_returns_all()
    test_collapse_stale_nudges_keeps_recent_and_anchor()
    test_trim_collapses_nudges()
    test_plan_compact_shape()
    test_current_state_injection_is_single_block()
    print("ok")
