"""Tests that prompt pack mode skips legacy injection layers."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.context_router import ContextRouter


def test_pack_mode_skips_phase_and_rag():
    messages = [{"role": "user", "content": "port scan 192.168.1.1"}]
    injections = ContextRouter.build_injections(
        messages,
        current_state="### CURRENT STATE ###\n[MISSION]\ntest\n#########",
        prompt_pack_mode=True,
        active_agent="recon",
    )
    combined = "\n".join(i.get("content", "") for i in injections)
    assert "RECONNAISSANCE" not in combined
    assert "DOMAIN REFERENCE" not in combined
    assert "TOOL PLAYBOOKS" not in combined
    assert "### CURRENT STATE ###" in combined


def test_pack_mode_schemas_match_active_agent():
    messages = [{"role": "user", "content": "try login"}]
    injections = ContextRouter.build_injections(
        messages,
        prompt_pack_mode=True,
        active_agent="web",
    )
    schemas = [i for i in injections if "RELATED TOOL SCHEMAS" in i.get("content", "")]
    assert len(schemas) == 1
    body = schemas[0]["content"]
    assert "http_get" in body or "try_http_login" in body
    assert "port_scan" not in body


def test_legacy_mode_still_has_phase_hint_or_schemas():
    messages = [{"role": "user", "content": "scan ports on 10.0.0.1"}]
    injections = ContextRouter.build_injections(messages, prompt_pack_mode=False)
    combined = "\n".join(i.get("content", "") for i in injections)
    assert "RELATED TOOL SCHEMAS" in combined or "port_scan" in combined.lower()
