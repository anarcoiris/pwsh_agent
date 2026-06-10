# Implementation Plan: Robustness Stack (Circuit Breaker, Scheduler, SQLite Session DB)

> **Status:** IMPLEMENTED. Adopting battle-tested robustness patterns inspired by NanoClaw v2.

---

## Plan A — Circuit Breaker (Crash-Loop Protection)

### Problem
`pulse.bat` launches `console.py` without crash-loop protection. If Ollama is down, config is broken, or a startup exception fires, the user can accidentally create a hot restart loop.

### Changes
- Created [core/circuit_breaker.py](file:///c:/Users/soyko/Documents/pwsh_agent/core/circuit_breaker.py) to track rapid startup crashes within a 1-hour window and delay subsequent launches using an exponential backoff.
- Wired startup and clean shutdown into [console.py](file:///c:/Users/soyko/Documents/pwsh_agent/console.py).
- Created [tests/test_circuit_breaker.py](file:///c:/Users/soyko/Documents/pwsh_agent/tests/test_circuit_breaker.py).

---

## Plan B — Scheduled Missions (Autonomous Recurring Tasks)

### Problem
Every mission requires manual operator input via the console REPL. There was no autonomous wake-up capability to run recurring tasks.

### Changes
- Created [core/scheduler.py](file:///c:/Users/soyko/Documents/pwsh_agent/core/scheduler.py) backed by SQLite (`.pulse/scheduler.db`) with 5-field cron parsing via `croniter`.
- Created [core/sweep_loop.py](file:///c:/Users/soyko/Documents/pwsh_agent/core/sweep_loop.py) to check for due missions every 60 seconds and run them asynchronously in the background.
- Wired interactive `schedule` command sub-menu and sweep loop into [console.py](file:///c:/Users/soyko/Documents/pwsh_agent/console.py).
- Created [tests/test_scheduler.py](file:///c:/Users/soyko/Documents/pwsh_agent/tests/test_scheduler.py).

---

## Plan C — SQLite Session State (Consolidated DB)

### Problem
Session state was scattered across 7+ JSON/MD files per session, creating fragile I/O paths, lack of cross-session querying, and risk of race conditions.

### Changes
- Created [core/session_db.py](file:///c:/Users/soyko/Documents/pwsh_agent/core/session_db.py) to consolidate messages history and key-value states in a per-session `session.db` SQLite database.
- Integrated `SessionDB` backend in [core/context.py](file:///c:/Users/soyko/Documents/pwsh_agent/core/context.py) (`AgentContextManager`) with transparent auto-migration of existing JSON files to SQLite on first access.
- Modified adapters to use `SessionDB` if `session.db` exists for:
  - [core/facts_store.py](file:///c:/Users/soyko/Documents/pwsh_agent/core/facts_store.py)
  - [core/task_plan.py](file:///c:/Users/soyko/Documents/pwsh_agent/core/task_plan.py)
  - [core/working_state.py](file:///c:/Users/soyko/Documents/pwsh_agent/core/working_state.py)
  - [core/intent_spec.py](file:///c:/Users/soyko/Documents/pwsh_agent/core/intent_spec.py)
  - [core/session_handoff.py](file:///c:/Users/soyko/Documents/pwsh_agent/core/session_handoff.py)
- Created [scripts/migrate_sessions_to_sqlite.py](file:///c:/Users/soyko/Documents/pwsh_agent/scripts/migrate_sessions_to_sqlite.py) one-time migration command-line utility.
- Created [tests/test_session_db.py](file:///c:/Users/soyko/Documents/pwsh_agent/tests/test_session_db.py).

---

## Verification

All tests run and pass cleanly:
```powershell
.venv\Scripts\python.exe -m pytest tests/test_circuit_breaker.py -v
.venv\Scripts\python.exe -m pytest tests/test_scheduler.py -v
.venv\Scripts\python.exe -m pytest tests/test_session_db.py -v
.venv\Scripts\python.exe -m pytest tests/ -v
```
