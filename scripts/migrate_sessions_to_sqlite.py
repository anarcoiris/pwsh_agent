"""scripts/migrate_sessions_to_sqlite.py — One-time session migration script.

Converts all existing session JSON/MD flat files into consolidated SQLite session.db files.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Add root folder to python path to resolve core imports
sys.path.append(str(Path(__file__).parent.parent.resolve()))

from core.session_paths import (
    app_root,
    list_session_ids,
    session_state_dir,
    facts_file,
    plan_state_file,
    intent_spec_file,
)
from core.working_state import _working_memory_path, current_state_file
from core.session_handoff import handoff_file
from core.session_db import SessionDB


def migrate_session(session_id: str, dry_run: bool = False, force: bool = False) -> bool:
    state_dir = session_state_dir(session_id)
    db_path = state_dir / "session.db"
    
    if db_path.is_file() and not force:
        print(f"[-] Session {session_id}: session.db already exists. Skipping (use --force to overwrite).")
        return False

    agent_json_path = state_dir / "agent_autonomous.json"
    
    # Check if there's anything to migrate
    state_mappings = {
        "facts": facts_file(session_id),
        "plan_state": plan_state_file(session_id),
        "intent_spec": intent_spec_file(session_id),
        "working_memory": _working_memory_path(session_id),
        "handoff": handoff_file(session_id),
    }
    cs_file = current_state_file(session_id)
    
    has_files = agent_json_path.is_file() or cs_file.is_file() or any(p.is_file() for p in state_mappings.values())
    if not has_files:
        print(f"[-] Session {session_id}: No legacy state files found to migrate.")
        return False

    print(f"[*] Session {session_id}: Migrating...")
    if dry_run:
        print(f"    [DRY RUN] Would create SQLite database at: {db_path}")
        if agent_json_path.is_file():
            print(f"    [DRY RUN] Would migrate messages from: {agent_json_path.name}")
        for key, path in state_mappings.items():
            if path.is_file():
                print(f"    [DRY RUN] Would migrate key '{key}' from: {path.name}")
        if cs_file.is_file():
            print(f"    [DRY RUN] Would migrate current_state_md from: {cs_file.name}")
        return True

    # Real migration
    try:
        # If forcing, unlink existing DB first
        if db_path.is_file() and force:
            for suffix in ("", "-wal", "-shm"):
                p = db_path.with_name(db_path.name + suffix)
                p.unlink(missing_ok=True)

        db = SessionDB(session_id)

        # 1. Migrate Messages
        if agent_json_path.is_file():
            try:
                with open(agent_json_path, encoding="utf-8") as f:
                    msgs = json.load(f)
                if isinstance(msgs, list):
                    for msg in msgs:
                        db.add_message(msg)
                    print(f"    [+] Migrated {len(msgs)} messages.")
                else:
                    print(f"    [!] Invalid messages format in {agent_json_path.name}")
            except Exception as e:
                print(f"    [!] Failed to migrate messages: {e}")

        # 2. Migrate Key-Value States
        for key, path in state_mappings.items():
            if path.is_file():
                try:
                    val = json.loads(path.read_text(encoding="utf-8"))
                    db.set_state(key, val)
                    print(f"    [+] Migrated key '{key}'.")
                except Exception as e:
                    print(f"    [!] Failed to migrate '{key}': {e}")

        # 3. Migrate CURRENT_STATE.md
        if cs_file.is_file():
            try:
                val = cs_file.read_text(encoding="utf-8")
                db.set_state("current_state_md", val)
                print("    [+] Migrated key 'current_state_md'.")
            except Exception as e:
                print(f"    [!] Failed to migrate 'current_state_md': {e}")

        db.close()

        # 4. Rename files to .bak
        all_to_rename = list(state_mappings.values()) + [agent_json_path, cs_file]
        for path in all_to_rename:
            if path.is_file():
                try:
                    path.rename(path.with_suffix(path.suffix + ".bak"))
                except Exception as e:
                    print(f"    [!] Failed to rename {path.name} to .bak: {e}")

        print(f"    [✓] Successfully migrated session {session_id} to SQLite.")
        return True
    except Exception as e:
        print(f"    [❌] Migration failed for session {session_id}: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Migrate JSON session files to SQLite session.db.")
    parser.add_argument("--session", type=str, help="Specific session ID to migrate (e.g. 20260604_120000)")
    parser.add_argument("--all", action="store_true", help="Migrate all sessions (default)")
    parser.add_argument("--dry-run", action="store_true", help="Print plans without executing them")
    parser.add_argument("--force", action="store_true", help="Force overwrite existing session.db files")

    args = parser.parse_args()

    sessions = []
    if args.session:
        sessions = [args.session]
    else:
        sessions = list_session_ids()

    if not sessions:
        print("No sessions found to migrate.")
        return

    migrated_count = 0
    for sid in sessions:
        if migrate_session(sid, dry_run=args.dry_run, force=args.force):
            migrated_count += 1

    print("-" * 50)
    action = "Would migrate" if args.dry_run else "Successfully migrated"
    print(f"[Summary] {action} {migrated_count} session(s).")


if __name__ == "__main__":
    main()
