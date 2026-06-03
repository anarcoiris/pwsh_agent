"""Tests for the canonical CURRENT STATE block and volatile working memory."""

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.working_state import (
    MAX_CURRENT_STATE_CHARS,
    WorkingMemory,
    build_current_state,
    load_working_memory,
    save_working_memory,
)


def test_fixed_section_order():
    block = build_current_state(
        mission="decode capture for credentials",
        plan={"next_action": "run analyze_pcapng", "phase": "extract", "done_steps": ["read_context"]},
        working_memory=WorkingMemory(last_observation="saw http form", current_hypothesis="creds in form"),
        last_tool_result="analyze_pcapng -> ok: {...}",
        draft="login_forms draft",
        facts_block="[SESSION FACTS]\npcap.path=x.pcapng",
        artifact_refs=["pcap: x.pcapng", "verbose_log: v.txt"],
    )
    order = [
        "[MISSION]",
        "[NEXT ACTION]",
        "[LAST TOOL RESULT]",
        "[DRAFT]",
        "[WORKING MEMORY]",
        "[COMPACT FACTS]",
        "[ARTIFACT REFS]",
    ]
    positions = [block.index(h) for h in order]
    assert positions == sorted(positions), "sections must appear in the fixed contract order"
    assert block.startswith("### CURRENT STATE ###")


def test_budget_enforced_under_huge_input():
    block = build_current_state(
        mission="m" * 50,
        plan={"next_action": "do thing", "last_failure": "boom"},
        working_memory=WorkingMemory(last_observation="o" * 5000),
        last_tool_result="t" * 50000,
        facts_block="f" * 50000,
        artifact_refs=["a" * 5000],
    )
    assert len(block) <= MAX_CURRENT_STATE_CHARS


def test_high_priority_preserved_when_truncated():
    block = build_current_state(
        mission="CRITICAL MISSION TEXT",
        plan={"next_action": "NEXT ACTION TEXT", "last_failure": "FAILURE TEXT"},
        working_memory=WorkingMemory(),
        facts_block="z" * 40000,
    )
    assert "CRITICAL MISSION TEXT" in block
    assert "NEXT ACTION TEXT" in block
    assert "FAILURE TEXT" in block


def test_facts_header_not_duplicated():
    block = build_current_state(
        mission="m",
        facts_block="[SESSION FACTS]\npcap.path=x",
    )
    # The standalone [SESSION FACTS] header is stripped; facts nest under COMPACT FACTS.
    assert "[SESSION FACTS]" not in block
    assert "pcap.path=x" in block
    assert "[COMPACT FACTS]" in block


def test_empty_inputs_produce_empty_block():
    assert build_current_state() == ""


def test_working_memory_roundtrip_and_clip():
    wm = WorkingMemory()
    wm.update(observation="x" * 1000, next_action="act", failure="bad")
    assert len(wm.last_observation) <= 400
    with tempfile.TemporaryDirectory() as tmp:
        with patch("core.session_paths.app_root", return_value=Path(tmp)):
            save_working_memory("wmsid", wm)
            loaded = load_working_memory("wmsid")
            assert loaded.next_action == "act"
            assert loaded.last_failure == "bad"


def test_working_memory_clear_strategy():
    wm = WorkingMemory(current_hypothesis="h", next_action="n", last_observation="o")
    wm.clear_strategy()
    assert wm.current_hypothesis == ""
    assert wm.next_action == ""
    assert wm.last_observation == "o"  # observation survives strategy change


def test_readaptation_section_after_last_failure():
    block = build_current_state(
        mission="crack hash mission",
        plan={"last_failure": "placeholder pwd.txt"},
        readaptation="[SYSTEM — PLAN READAPTATION REQUIRED]\nReadapt now:",
    )
    assert "[READAPTATION]" in block
    assert "READAPTATION REQUIRED" in block
    fail_pos = block.index("[LAST FAILURE]")
    readapt_pos = block.index("[READAPTATION]")
    assert fail_pos < readapt_pos


def test_readaptation_respects_budget():
    block = build_current_state(
        mission="m",
        readaptation="x" * 10000,
    )
    assert len(block) <= MAX_CURRENT_STATE_CHARS


def test_long_session_state_stays_bounded():
    """Scenario C: 30 synthetic updates must not grow CURRENT STATE."""
    wm = WorkingMemory()
    sizes = []
    for i in range(30):
        wm.update(observation=f"observation step {i} " + "x" * 200, next_action=f"step {i}")
        block = build_current_state(
            mission="long session mission",
            plan={"next_action": f"step {i}", "phase": "loop", "done_steps": [f"s{j}" for j in range(i)]},
            working_memory=wm,
            last_tool_result=f"tool{i} -> ok: " + "y" * 500,
            facts_block="[SESSION FACTS]\n" + "\n".join(f"k{j}=v{j}" for j in range(i)),
            artifact_refs=[f"art{j}: p{j}.txt" for j in range(i)],
        )
        sizes.append(len(block))
    assert max(sizes) <= MAX_CURRENT_STATE_CHARS


if __name__ == "__main__":
    test_fixed_section_order()
    test_budget_enforced_under_huge_input()
    test_high_priority_preserved_when_truncated()
    test_facts_header_not_duplicated()
    test_empty_inputs_produce_empty_block()
    test_working_memory_roundtrip_and_clip()
    test_working_memory_clear_strategy()
    test_long_session_state_stays_bounded()
    print("ok")
