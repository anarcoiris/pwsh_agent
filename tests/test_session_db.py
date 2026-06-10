"""Tests for core.session_db and SQLite session integration."""

import json
import shutil
from pathlib import Path
import pytest

from core.session_db import SessionDB
from core.context import AgentContextManager
from core.facts_store import load_facts, save_facts
from core.task_plan import load_plan_state, save_plan_state, TaskPlanTracker, TaskStep
from core.working_state import load_working_memory, save_working_memory, WorkingMemory
from scripts.migrate_sessions_to_sqlite import migrate_session


@pytest.fixture
def temp_session(tmp_path, monkeypatch):
    """Fixture to redirect app_root and create a clean session ID."""
    import uuid
    session_id = f"test_session_{uuid.uuid4().hex[:8]}"
    fake_root = tmp_path / "app_root"
    fake_root.mkdir(exist_ok=True)
    
    # Patch app_root in runtime_paths, session_paths, and context
    monkeypatch.setattr("core.runtime_paths.app_root", lambda: fake_root)
    monkeypatch.setattr("core.session_paths.app_root", lambda: fake_root)
    monkeypatch.setattr("core.context.app_root", lambda: fake_root)
    
    yield session_id
    
    # Cleanup temp directory
    shutil.rmtree(fake_root, ignore_errors=True)


def test_session_db_basic_ops(temp_session):
    db = SessionDB(temp_session)
    
    # Check count on empty DB
    assert db.count() == 0
    
    # Add messages
    m1 = {"role": "system", "content": "System message"}
    m2 = {"role": "user", "content": "Hello agent"}
    m3 = {"role": "assistant", "content": "Hello user", "tool_calls": [{"name": "some_tool"}]}
    
    seq1 = db.add_message(m1)
    seq2 = db.add_message(m2)
    seq3 = db.add_message(m3)
    
    assert seq1 == 1
    assert seq2 == 2
    assert seq3 == 3
    assert db.count() == 3
    
    # Retrieve messages
    msgs = db.get_messages()
    assert len(msgs) == 3
    assert msgs[0]["role"] == "system"
    assert msgs[0]["content"] == "System message"
    assert msgs[1]["role"] == "user"
    assert msgs[2]["role"] == "assistant"
    assert msgs[2]["tool_calls"] == [{"name": "some_tool"}]
    
    # Test update
    db.update_message(seq2, "New user message")
    msgs = db.get_messages()
    assert msgs[1]["content"] == "New user message"
    
    # Test limit/get_recent
    recent = db.get_recent(2)
    assert len(recent) == 2
    assert recent[0]["seq"] == 2
    assert recent[1]["seq"] == 3
    
    db.close()


def test_session_db_state_ops(temp_session):
    db = SessionDB(temp_session)
    
    # Check missing key
    assert db.get_state("non_existent") is None
    
    # Save & load dict/list
    test_dict = {"a": 1, "b": [2, 3]}
    db.set_state("my_key", test_dict)
    assert db.get_state("my_key") == test_dict
    
    # Overwrite state
    db.set_state("my_key", "simple_string")
    assert db.get_state("my_key") == "simple_string"
    
    # Delete state
    db.delete_state("my_key")
    assert db.get_state("my_key") is None
    
    db.close()


def test_session_db_cross_search(temp_session, tmp_path, monkeypatch):
    from core.session_paths import session_state_dir
    
    # We will create two sessions and search across them
    s1 = f"{temp_session}_one"
    s2 = f"{temp_session}_two"
    
    db1 = SessionDB(s1)
    db1.add_message({"role": "user", "content": "Find secret key in PCAP file"})
    db1.close()
    
    db2 = SessionDB(s2)
    db2.add_message({"role": "assistant", "content": "Extracted secret key value is 0xFEED"})
    db2.close()
    
    # Search
    results = SessionDB.search_messages("secret key")
    # Verify results are sorted by session_id/seq
    session_ids = [r["session_id"] for r in results]
    assert s1 in session_ids
    assert s2 in session_ids


def test_agent_context_manager_sqlite(temp_session):
    # Instantiate AgentContextManager which will initialize SQLite DB automatically
    # since state_path is None (defaulting to session.db)
    mgr = AgentContextManager(session_id=temp_session)
    assert mgr.use_sqlite is True
    assert mgr.db is not None
    
    # Messages round-trip
    mgr.add_message({"role": "user", "content": "Init context"})
    mgr.save_state()
    mgr.db.close()
    
    # Reload and check
    mgr2 = AgentContextManager(session_id=temp_session)
    assert len(mgr2.messages) == 1
    assert mgr2.messages[0]["content"] == "Init context"
    
    # Clear history
    mgr2.clear_history()
    assert len(mgr2.messages) == 0
    mgr2.db.close()
    
    mgr3 = AgentContextManager(session_id=temp_session)
    assert len(mgr3.messages) == 0
    mgr3.db.close()


def test_adapters_dispatch(temp_session):
    # Initialize the SQLite session.db by instantiating AgentContextManager
    mgr = AgentContextManager(session_id=temp_session)
    
    # Test facts adapter
    facts = {"credentials": [{"user": "admin", "password": "123"}], "hosts": {"live": ["127.0.0.1"]}}
    save_facts(temp_session, facts)
    
    loaded_facts = load_facts(temp_session)
    assert loaded_facts["hosts"]["live"] == ["127.0.0.1"]
    assert loaded_facts["credentials"][0]["user"] == "admin"
    
    # Test working memory adapter
    wm = WorkingMemory(last_observation="Port 80 is open", current_hypothesis="Vulnerable")
    save_working_memory(temp_session, wm)
    
    loaded_wm = load_working_memory(temp_session)
    assert loaded_wm.last_observation == "Port 80 is open"
    assert loaded_wm.current_hypothesis == "Vulnerable"
    
    # Test task plan tracker adapter
    tracker = TaskPlanTracker(
        prompt="Scan ports",
        steps=[TaskStep("scan", "Port scanning", "port_scan")]
    )
    save_plan_state(temp_session, tracker)
    
    loaded_tracker = load_plan_state(temp_session)
    assert loaded_tracker is not None
    assert loaded_tracker.prompt == "Scan ports"
    assert loaded_tracker.steps[0].id == "scan"
    
    mgr.db.close()


def test_auto_migration(tmp_path, monkeypatch):
    import uuid
    session_id = f"migration_test_{uuid.uuid4().hex[:8]}"
    fake_root = tmp_path / "app_root"
    fake_root.mkdir()
    monkeypatch.setattr("core.runtime_paths.app_root", lambda: fake_root)
    monkeypatch.setattr("core.session_paths.app_root", lambda: fake_root)
    monkeypatch.setattr("core.context.app_root", lambda: fake_root)
    
    from core.session_paths import facts_file, plan_state_file, session_state_dir
    state_dir = session_state_dir(session_id)
    state_dir.mkdir(parents=True, exist_ok=True)
    
    # 1. Create legacy JSON files
    agent_json = state_dir / "agent_autonomous.json"
    agent_json.write_text(json.dumps([{"role": "user", "content": "Legacy msg"}]), encoding="utf-8")
    
    facts_json = facts_file(session_id)
    facts_json.write_text(json.dumps({"hosts": {"live": ["192.168.1.1"]}}), encoding="utf-8")
    
    # 2. Check that session.db does NOT exist
    db_path = state_dir / "session.db"
    assert not db_path.exists()
    
    # 3. Instantiate AgentContextManager (triggers auto-migration)
    mgr = AgentContextManager(session_id=session_id)
    
    # 4. Check migration results
    assert db_path.is_file()
    assert len(mgr.messages) == 1
    assert mgr.messages[0]["content"] == "Legacy msg"
    
    # Verify legacy files were renamed to .bak
    assert not agent_json.exists()
    assert agent_json.with_suffix(".json.bak").is_file()
    assert not facts_json.exists()
    assert facts_json.with_suffix(".json.bak").is_file()
    
    # Verify facts load correctly from SQLite
    loaded = load_facts(session_id)
    assert loaded["hosts"]["live"] == ["192.168.1.1"]
    
    mgr.db.close()
