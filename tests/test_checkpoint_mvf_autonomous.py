"""Tests for mvf_autonomous checkpoint profile (R2)."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.user_checkpoint import (
    CheckpointDecision,
    CheckpointGate,
    CheckpointTrigger,
    append_operator_inbox,
    operator_inbox_path,
    parse_user_decision,
    read_operator_inbox_decision,
)


def _cfg(profile: str = "mvf_autonomous") -> dict:
    return {
        "checkpoint": {
            "enabled": True,
            "profile": profile,
            "triggers": [t.value for t in CheckpointTrigger if t != CheckpointTrigger.PROMOTE_GATE],
        }
    }


def test_mvf_autonomous_should_fire_false():
    gate = CheckpointGate("sess_mvf", _cfg())
    assert gate.notify_only is True
    for trigger in CheckpointTrigger:
        if trigger == CheckpointTrigger.PROMOTE_GATE:
            continue
        assert gate.should_fire(trigger) is False


@pytest.mark.asyncio
async def test_mvf_autonomous_maybe_checkpoint_returns_continue(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    gate = CheckpointGate("sess_cont", _cfg())
    ask = AsyncMock(return_value="x")

    decision = await gate.maybe_checkpoint(
        CheckpointTrigger.STALL_RECOVERY,
        detail="stall test",
        ask_user_fn=ask,
    )

    assert decision == CheckpointDecision.CONTINUE
    ask.assert_not_called()


@pytest.mark.asyncio
async def test_mvf_autonomous_writes_inbox(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_id = "sess_inbox"
    gate = CheckpointGate(session_id, _cfg())

    await gate.maybe_checkpoint(
        CheckpointTrigger.NEEDS_READAPTATION,
        detail="step failed",
        ask_user_fn=AsyncMock(),
    )

    path = operator_inbox_path(session_id)
    assert path.is_file()
    row = json.loads(path.read_text(encoding="utf-8").strip())
    assert row["trigger"] == "needs_readaptation"
    assert "step failed" in row["detail"]
    assert row["source"] == "agent"


def test_parse_stop_tokens_extended():
    assert parse_user_decision("cancel") == CheckpointDecision.STOP
    assert parse_user_decision("no") == CheckpointDecision.STOP
    assert parse_user_decision("abort") == CheckpointDecision.STOP
    assert parse_user_decision("no hagas") == CheckpointDecision.STOP


def test_operator_inbox_stop_reply(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_id = "sess_stop"
    append_operator_inbox(session_id, CheckpointTrigger.STALL_RECOVERY, "stall")
    path = operator_inbox_path(session_id)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"role": "operator_reply", "text": "stop"}) + "\n")

    assert read_operator_inbox_decision(session_id) == CheckpointDecision.STOP


@pytest.mark.asyncio
async def test_orchestrator_applies_profile(monkeypatch):
    import types

    cron_mod = types.ModuleType("croniter")

    class _CronIter:
        def __init__(self, *args, **kwargs):
            pass

        def get_next(self, *_args, **_kwargs):
            return 0.0

    cron_mod.croniter = _CronIter
    monkeypatch.setitem(sys.modules, "croniter", cron_mod)

    from core import orchestrator as orch

    captured: dict = {}

    class FakeAgent:
        def __init__(self):
            self.config = {"checkpoint": {"profile": "headless", "enabled": True, "triggers": []}}
            self.active_specialist = "lead"
            self.network_mode = "SANDBOX"
            self.session_id = "interactive"
            self.ask_user_fn = None
            self._active_queue_job_id = None

        def _init_system_prompt(self):
            pass

        def begin_queue_job_session(self, job_id: str) -> str:
            self.session_id = f"q_{job_id}"
            return self.session_id

        async def run_mission(self, text, cb):
            captured["profile"] = self.config["checkpoint"]["profile"]
            captured["session_id"] = self.session_id
            captured["job_id"] = self._active_queue_job_id
            captured["ask_user"] = self.ask_user_fn

    fake = FakeAgent()
    monkeypatch.setattr(orch, "mark_job_running", lambda _id: None)
    monkeypatch.setattr(orch, "mark_job_completed", lambda _id: None)

    job = {
        "id": "abc123",
        "job_type": "pwsh_mission",
        "payload": {"mission_text": "test mission"},
        "checkpoint_profile": "mvf_autonomous",
    }

    await orch.execute_job(job, fake)

    assert captured["profile"] == "mvf_autonomous"
    assert captured["session_id"] == "q_abc123"
    assert captured["job_id"] == "abc123"
    assert captured["ask_user"] is not None
