"""Tests for core.scheduler and core.sweep_loop."""

import asyncio
import shutil
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from core.scheduler import (
    schedule_mission,
    list_scheduled,
    get_due_missions,
    mark_completed,
    mark_failed,
    pause_mission,
    resume_mission,
    cancel_mission,
    validate_cron,
    _db_path,
)
from core.sweep_loop import sweep_loop


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    """Fixture to isolate the scheduler database in a temp directory."""
    fake_root = tmp_path / "app_root"
    fake_root.mkdir()
    
    # Patch app_root in both runtime_paths and scheduler module namespace
    monkeypatch.setattr("core.runtime_paths.app_root", lambda: fake_root)
    monkeypatch.setattr("core.scheduler.app_root", lambda: fake_root)
    
    yield
    
    # Clean up
    shutil.rmtree(fake_root, ignore_errors=True)


def test_validate_cron():
    assert validate_cron("* * * * *") is True
    assert validate_cron("0 9 * * 1-5") is True
    assert validate_cron("invalid cron") is False
    assert validate_cron("invalid * * * *") is False


def test_schedule_one_shot():
    # Immediate execution default
    mid = schedule_mission("Scan port 22 on localhost")
    assert len(mid) == 12
    
    # Check it is in active list
    lst = list_scheduled()
    assert len(lst) == 1
    assert lst[0]["id"] == mid
    assert lst[0]["status"] == "active"
    assert lst[0]["cron_expr"] is None
    assert lst[0]["max_runs"] == 1
    
    # Check it is due immediately
    due = get_due_missions()
    assert len(due) == 1
    assert due[0]["id"] == mid


def test_schedule_recurring():
    mid = schedule_mission("Check CPU usage", cron_expr="*/5 * * * *")
    
    lst = list_scheduled()
    assert len(lst) == 1
    assert lst[0]["id"] == mid
    assert lst[0]["cron_expr"] == "*/5 * * * *"
    assert lst[0]["max_runs"] is None


def test_pause_resume_cancel():
    mid = schedule_mission("Clean temp files")
    
    # Pause
    assert pause_mission(mid) is True
    assert len(get_due_missions()) == 0
    assert list_scheduled()[0]["status"] == "paused"
    
    # Resume
    assert resume_mission(mid) is True
    assert len(get_due_missions()) == 1
    assert list_scheduled()[0]["status"] == "active"
    
    # Cancel (delete)
    assert cancel_mission(mid) is True
    assert len(list_scheduled()) == 0


def test_execution_lifecycle():
    # Test one-shot transitions to done
    mid = schedule_mission("Run audit")
    due = get_due_missions()
    assert len(due) == 1
    
    mark_completed(mid)
    
    due2 = get_due_missions()
    assert len(due2) == 0
    
    lst = list_scheduled(include_done=True)
    assert lst[0]["status"] == "done"
    assert lst[0]["run_count"] == 1


def test_max_runs_limit():
    # Test recurring limit
    mid = schedule_mission("Log memory", cron_expr="*/10 * * * *", max_runs=2)
    
    mark_completed(mid)
    assert list_scheduled()[0]["status"] == "active"
    assert list_scheduled()[0]["run_count"] == 1
    
    mark_completed(mid)
    assert list_scheduled(include_done=True)[0]["status"] == "done"
    assert list_scheduled(include_done=True)[0]["run_count"] == 2


def test_mark_failed():
    mid = schedule_mission("Scan vulnerable target")
    
    mark_failed(mid, "Target unreachable")
    
    # Stays active but records error
    lst = list_scheduled()
    assert lst[0]["status"] == "active"
    assert lst[0]["last_error"] == "Target unreachable"
    assert lst[0]["last_run_at"] is not None


@pytest.mark.anyio
async def test_sweep_loop_execution():
    # Setup mock agent
    mock_agent = MagicMock()
    mock_agent.run_mission = AsyncMock(return_value="Success")
    mock_agent.active_specialist = "lead"
    mock_agent.network_mode = "SANDBOX"
    mock_agent.session_id = "initial_session"
    
    # Schedule one-shot
    mid = schedule_mission("Ping localhost", specialist="network", network_mode="HOST")
    
    # Run sweep loop for one iteration by checking get_due_missions inside the loop
    # We will patch asyncio.sleep to break the loop or run it once
    with patch("core.sweep_loop.asyncio.sleep", AsyncMock()) as mock_sleep:
        # Mock sleep to raise GeneratorExit or CancelledError to break the infinite loop after 1 run
        mock_sleep.side_effect = [None, asyncio.CancelledError()]
        
        try:
            await sweep_loop(mock_agent, interval_s=1)
        except asyncio.CancelledError:
            pass
            
    # Verify mock agent was called with correct text
    from unittest.mock import ANY
    mock_agent.run_mission.assert_called_once_with("Ping localhost", ANY)
    
    # Verify agent attributes were temporarily changed and restored
    assert mock_agent.active_specialist == "lead"
    assert mock_agent.network_mode == "SANDBOX"
    
    # Verify mission is marked completed in DB
    lst = list_scheduled(include_done=True)
    assert lst[0]["status"] == "done"
    assert lst[0]["run_count"] == 1
