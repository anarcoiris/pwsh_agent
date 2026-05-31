"""Tests for context trimming, compaction, anchor query, and ResultCompactor."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.context import (
    AgentContextManager,
    ContextCompactor,
    DIGEST_PREFIX,
    estimate_chars,
    estimate_tokens,
)
from core.context_router import ContextRouter
from core.llm_utils import DynamicContextBuilder, ResultCompactor
from core.query_anchor import is_system_directive, resolve_anchor_query
from core.task_intent import detect_mission_kind


def test_is_system_directive():
    assert is_system_directive("[SYSTEM] You are stalling.")
    assert not is_system_directive("Analyze last_capture.pcapng for login passwords")


def test_resolve_anchor_skips_nudges():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "decode last_capture.pcapng for login and xmlObj"},
        {"role": "user", "content": "[SYSTEM] Continue investigation now."},
    ]
    assert "pcapng" in resolve_anchor_query(messages).lower()


def test_anchor_phase_pcap_with_trailing_nudge():
    messages = [
        {"role": "user", "content": "analyze last_capture.pcapng for HTTP login credentials"},
        {"role": "user", "content": "[SYSTEM] stall recovery — run analyze_pcapng"},
    ]
    hint = DynamicContextBuilder.build_context(messages)
    assert "PCAP ANALYSIS" in hint


def test_build_injections_pcap_phase_after_nudge():
    anchor = "analyze last_capture.pcapng for credentials"
    messages = [
        {"role": "user", "content": anchor},
        {"role": "user", "content": "[SYSTEM] stall"},
    ]
    injections = ContextRouter.build_injections(messages, anchor_query=anchor)
    phase = [i for i in injections if "CURRENT PHASE" in i.get("content", "")]
    assert phase
    assert "PCAP" in phase[0]["content"]


def test_detect_mission_kind():
    assert detect_mission_kind("crack sha256 hash with crack_hash") == "hash"
    assert detect_mission_kind("analyze foo.pcapng") == "pcap"
    assert detect_mission_kind("write watcher.py script") == "dev"


def test_digest_includes_artifact_pointers():
    from core.context import _tool_digest_line

    spill = json.dumps({
        "success": True,
        "artifact_file": "state/sessions/s1/artifacts/host_exec_1.txt",
        "artifact_bytes": 50000,
    })
    line = _tool_digest_line("host_exec", spill)
    assert "artifact_file=" in line

    pcap = json.dumps({
        "success": True,
        "analysis": {"verbose_log_file": ".pulse/pcap_logs/verbose_1.txt"},
    })
    line2 = _tool_digest_line("analyze_pcapng", pcap)
    assert "verbose_log_file=" in line2


def test_digest_replaced_not_stacked():
    msgs = [{"role": "system", "content": "s"}]
    for i in range(15):
        msgs.append({"role": "tool", "name": "host_exec", "content": f"out{i}" * 500})
    first = ContextCompactor.compact_old_tool_results(msgs, max_context_chars=5000, session_id="test_digest")
    digests = [m for m in first if str(m.get("content", "")).startswith(DIGEST_PREFIX)]
    assert len(digests) == 1
    second = ContextCompactor.compact_old_tool_results(first, max_context_chars=5000, session_id="test_digest")
    digests2 = [m for m in second if str(m.get("content", "")).startswith(DIGEST_PREFIX)]
    assert len(digests2) == 1


def test_char_cap_enforced():
    mgr = AgentContextManager(
        session_id="test_cap",
        max_total_context=80,
        max_context_chars=8000,
        max_tool_result_chars=4000,
        state_path=str(Path(__file__).parent / "_tmp_agent_test_cap.json"),
    )
    mgr.clear_history()
    mgr.messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "mission"},
        {"role": "tool", "name": "x", "content": "Z" * 60_000},
    ]
    mgr.trim_context()
    assert estimate_chars(mgr.messages) <= 8000 + 500
    try:
        mgr.state_path.unlink(missing_ok=True)
    except OSError:
        pass


def test_token_estimate_and_budget_trim():
    mgr = AgentContextManager(
        session_id="test_token",
        max_total_context=200,
        max_context_chars=100000,
        max_tool_result_chars=22000,
        max_context_tokens=1000,
        reserve_generation_tokens=300,
        reserve_injection_tokens=200,
        state_path=str(Path(__file__).parent / "_tmp_agent_test_token.json"),
    )
    mgr.clear_history()
    mgr.messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "mission"}]
    for _ in range(30):
        mgr.messages.append({"role": "assistant", "content": "a" * 700})
        mgr.messages.append({"role": "tool", "name": "host_exec", "content": "b" * 700})
    before = estimate_tokens(mgr.messages)
    mgr.trim_context()
    after = estimate_tokens(mgr.messages)
    assert after < before
    try:
        mgr.state_path.unlink(missing_ok=True)
    except OSError:
        pass


def test_turn_trim_preserves_assistant_tool_pair():
    mgr = AgentContextManager(
        session_id="test_turn",
        max_total_context=80,
        max_context_chars=3000,
        max_tool_result_chars=2000,
        state_path=str(Path(__file__).parent / "_tmp_agent_test_turn.json"),
    )
    mgr.clear_history()
    turns = []
    for _ in range(8):
        turns.extend([
            {"role": "assistant", "content": "ok", "tool_calls": [{"function": {"name": "host_exec"}}]},
            {"role": "tool", "name": "host_exec", "content": "x" * 800},
        ])
    mgr.messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "go"}] + turns
    mgr.trim_context()
    msgs = mgr.messages
    for i, m in enumerate(msgs):
        if m.get("role") == "tool":
            if i == 0 or msgs[i - 1].get("role") != "assistant":
                if not (i > 1 and msgs[i - 1].get("role") == "tool"):
                    raise AssertionError(f"orphan tool at {i}")
    try:
        mgr.state_path.unlink(missing_ok=True)
    except OSError:
        pass


def test_pcap_compaction_keeps_priority_keys():
    ResultCompactor.configure_max_chars(22_000)
    analysis = {
        "key_fields": "login=user1 password=secret",
        "potential_plaintext_credentials": "user: admin pass: test",
        "http_forms": "form data",
        "packet_summary": "X" * 100_000,
        "verbose_log_file": ".pulse/pcap_logs/verbose_1.txt",
    }
    payload = json.dumps({"success": True, "analysis": analysis})
    out = ResultCompactor.compact("analyze_pcapng", payload)
    assert "login=user1" in out or "key_fields" in out
    assert "potential_plaintext" in out.lower() or "admin" in out
    assert len(out) <= 22_500
