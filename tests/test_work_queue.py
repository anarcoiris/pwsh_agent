"""Tests for unified work queue."""

import shutil
import pytest

from core.work_queue import (
    enqueue_job,
    list_jobs,
    get_runnable_jobs,
    pause_job,
    resume_job,
    cancel_job,
    mark_job_completed,
    _db_path,
)


@pytest.fixture(autouse=True)
def temp_queue_db(tmp_path, monkeypatch):
    fake_root = tmp_path / "app_root"
    fake_root.mkdir()
    monkeypatch.setattr("core.runtime_paths.app_root", lambda: fake_root)
    monkeypatch.setattr("core.work_queue.app_root", lambda: fake_root)
    yield
    shutil.rmtree(fake_root, ignore_errors=True)


def test_enqueue_and_list():
    jid = enqueue_job(
        "pwsh_mission",
        {"mission_text": "test mission", "specialist": "lead"},
        priority=60,
    )
    jobs = list_jobs()
    assert len(jobs) == 1
    assert jobs[0]["id"] == jid
    assert jobs[0]["payload"]["mission_text"] == "test mission"


def test_runnable_immediate():
    enqueue_job("pwsh_mission", {"mission_text": "now"})
    runnable = get_runnable_jobs()
    assert len(runnable) >= 1


def test_pause_resume_cancel():
    jid = enqueue_job("hygiene_scan", {"repo_path": "/tmp/x"})
    assert pause_job(jid)
    assert list_jobs()[0]["status"] == "paused"
    assert resume_job(jid)
    assert list_jobs()[0]["status"] == "pending"
    assert cancel_job(jid)
    assert list_jobs() == []


def test_mark_completed_one_shot():
    jid = enqueue_job("pwsh_mission", {"mission_text": "once"})
    mark_job_completed(jid)
    jobs = list_jobs(include_done=True)
    assert jobs[0]["status"] == "done"
