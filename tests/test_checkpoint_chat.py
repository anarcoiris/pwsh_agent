"""Tests for CheckpointGate integration inside chat_turn."""
import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import ReActAgent
from core.user_checkpoint import CheckpointDecision


async def _run_chat_checkpoint_stop_test():
    ag = ReActAgent()
    ag.config = ag.config.copy()
    ag.config.setdefault("checkpoint", {})
    ag.config["checkpoint"]["enabled"] = True
    # Reset/ensure checkpoint gate is active for all triggers
    from core.user_checkpoint import CheckpointGate
    ag._checkpoint_gate = CheckpointGate(ag.session_id, ag.config)

    # Mock the LLM to output a host_exec tool call on the first turn
    async def mock_chat(*args, **kwargs):
        return {
            "message": {
                "role": "assistant",
                "content": "Running host_exec",
                "tool_calls": [
                    {
                        "function": {
                            "name": "host_exec",
                            "arguments": {"command": "echo test_checkpoint"}
                        }
                    }
                ]
            }
        }
    ag.adapter.chat = mock_chat

    # Mock tool execution to just return success without running a real process
    async def mock_execute_tool(name, args, tools_called, step_callback=None):
        # We simulate the success of execution and manually trigger the checkpoint trigger
        # exactly like agent.py does for host_exec
        ag._checkpoint_decision = CheckpointDecision.STOP
        return True, 1

    ag._execute_tool = mock_execute_tool

    # Mock ask_user_fn to log calls and return stop
    asked_messages = []
    async def mock_ask(message):
        asked_messages.append(message)
        return "x"  # 'x' maps to STOP

    ag.ask_user_fn = mock_ask

    res = await ag.chat_turn("execute dummy command")
    
    # Checkpoint decision should have broken the loop early
    assert ag._checkpoint_decision is None
    # We should have processed tool execution which set STOP
    # and the next iteration or loop end caught STOPPED
    # (since _checkpoint_decision = STOP was set in _execute_tool, and chat_turn loop handles it)


def test_chat_turn_checkpoint_stop():
    asyncio.run(_run_chat_checkpoint_stop_test())


if __name__ == "__main__":
    test_chat_turn_checkpoint_stop()
    print("ok")
