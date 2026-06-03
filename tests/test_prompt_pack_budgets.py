"""Token budget tests for the 4-file prompt contract."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.context import estimate_tokens
from core.prompt_pack import PromptPack, PromptBudgets, build_tools_md, trim_to_token_budget
from core.working_state import build_current_state, MAX_CURRENT_STATE_CHARS


def _tokens(text: str) -> int:
    return estimate_tokens([{"role": "system", "content": text}])


def test_trim_to_token_budget():
    long = "x" * 10000
    trimmed = trim_to_token_budget(long, 100)
    assert len(trimmed) <= 100 * 4
    assert trimmed.endswith("...")


def test_agents_md_within_budget():
    pack = PromptPack()
    text = pack.load_agents_md()
    trimmed = trim_to_token_budget(text, pack.budgets.agents_tokens)
    assert _tokens(trimmed) <= pack.budgets.agents_tokens


def test_soul_md_within_budget():
    pack = PromptPack()
    text = pack.load_soul_md()
    trimmed = trim_to_token_budget(text, pack.budgets.soul_tokens)
    assert _tokens(trimmed) <= pack.budgets.soul_tokens


def test_tools_md_each_agent_within_budget():
    pack = PromptPack()
    for agent_id in ("lead", "workspace", "web", "recon", "forensic", "crypto"):
        md = build_tools_md(agent_id)
        trimmed = trim_to_token_budget(md, pack.budgets.tools_tokens)
        assert _tokens(trimmed) <= pack.budgets.tools_tokens, agent_id


def test_assembled_system_excludes_identity():
    pack = PromptPack()
    prompt = pack.assemble_system(active_agent="lead", session_id="test")
    assert "IDENTITY.md" not in prompt
    assert "USER.md" not in prompt
    assert "### AGENTS ###" in prompt
    assert "### SOUL ###" in prompt
    assert "### TOOLS ###" in prompt


def test_current_state_worst_case_within_budget():
    block = build_current_state(
        mission="m" * 600,
        active_agent="web",
        handoff_brief="brief " * 200,
        return_to_lead_when="when login succeeds",
        declared_intent={
            "domain": "web_auth",
            "summary": "test login",
            "objectives": ["obj"] * 10,
            "targets": ["192.168.1.1"] * 10,
            "success_criteria": ["200 OK"] * 5,
        },
        plan={"next_action": "try_http_login", "last_failure": "fail"},
        working_memory=None,
        last_tool_result="r" * 50000,
        facts_block="f" * 50000,
        artifact_refs=["a" * 500] * 20,
        max_chars=PromptBudgets().current_state_tokens * 4,
    )
    assert len(block) <= PromptBudgets().current_state_tokens * 4
    assert "active_agent=web" in block
    assert "[HANDOFF]" in block or "brief" in block


def test_active_agent_preserved_when_truncated():
    block = build_current_state(
        mission="CRITICAL",
        active_agent="forensic",
        handoff_brief="analyze pcap now",
        facts_block="z" * 40000,
        max_chars=MAX_CURRENT_STATE_CHARS,
    )
    assert "active_agent=forensic" in block
    assert "analyze pcap" in block
