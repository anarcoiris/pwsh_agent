"""Tests for forbid_network enforcement in _execute_tool."""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import ReActAgent
from core.task_intent import TaskIntent


async def _run_block_test():
    ag = ReActAgent()
    ag._active_intent = TaskIntent(forbid_network=True, deliverables=[], is_dev_task=True)
    ag.ctx_manager.clear_history()
    did_exec, delta = await ag._execute_tool(
        "port_scan",
        {"target": "127.0.0.1"},
        set(),
    )
    assert not did_exec
    assert delta == 0
    last = ag.ctx_manager.get_messages()[-1]
    payload = json.loads(last["content"])
    assert payload.get("success") is False
    assert "not allowed" in payload.get("error", "").lower()


def test_forbid_network_blocks_port_scan():
    asyncio.run(_run_block_test())


if __name__ == "__main__":
    test_forbid_network_blocks_port_scan()
    print("ok")
