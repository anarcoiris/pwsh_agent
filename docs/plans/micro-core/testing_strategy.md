# Micro-Core Testing Strategy

**Status:** PROPOSAL  
**Date:** 2026-06-10

Testing approach for the script-builder micro agent, borrowed from Pulse's layered validation.

---

## Test layers

### 1. Unit tests

| Area | Pulse reference | Assert |
|------|-----------------|--------|
| Plan parsing | `tests/test_task_plan.py` | Scripting domain emits full plan template, not single `write_file` |
| Error signatures | `tests/test_retry_executor.py` | Same PS error at different line → same signature |
| Attempt cap | `tests/test_retry_executor.py` | 8 failures → `BLOCKED`, roadmap advances |
| Success gating | `tests/test_retry_executor.py` | `exit_code != 0` does not mark step done |
| Deliverable guards | `tests/test_prompt_pack_budgets.py`, write guard tests | Wrong path blocked; pending deliverables enforced |
| PS sanitizer | `tests/test_ps1_sanitize.py` | Backtick line-continuation fixes on write |
| Parser | `tests/test_parser_fix.py` | Fenced code blocks → `write_file` |

### 2. Routing tests

| Input | Expected dispatch |
|-------|-------------------|
| `.py` script run | `run_script` |
| `.ps1` script run | `host_exec` |
| `powershell -File foo.py` | rejected or redirected to `run_script` |
| Progress note to `workspace/plan.md` | `append_note`, not `write_file` |

### 3. Golden intent set (~20 prompts)

Assert `domain`, `deliverables`, and `capabilities` for prompts like:

- "Write ping_sweep.ps1 that pings 192.168.1.1–5 and logs results"
- "Create a Python watcher for C:\logs using watchdog"
- "Fix the broken script in workspace/foo.ps1"
- "Build install_task.ps1 to register a scheduled task"
- "Review auth.py" → should **not** appear in micro agent scope (out of domain or rejected)

Must classify scripting prompts as `scripting` / `file_write`, never `hash` or `pcap`.

### 4. Live mission harness

Pattern from `tools_dev/validate_mission.py`:

```powershell
python tools_dev/validate_mission.py pingsweep
python tools_dev/validate_mission.py python_watcher
python tools_dev/validate_mission.py fix_broken_ps1
```

Each mission reports:

- Tool call trace (`step_callback`)
- Final reply excerpt
- `llm_audit` telemetry (`ctx_saturation`, parser paths)
- Artifacts on disk

**Pass criteria:**

- Deliverable file exists
- Last run exit code 0
- Output artifact matches expected content (where applicable)
- `ctx_saturation` < 0.95
- Attempts ≤ 8 per step (or BLOCKED with strategy-change message)

### 5. Telemetry

Track per session:

- `ctx_saturation` worst case
- Attempt counts per step
- Time-to-green (steps until exit 0)
- Parser paths used (`native_tool_calls` vs text fallback)

Flip `agent.llm_audit` to `meta` after validation to avoid quadratic logs.

---

## Golden missions (initial set)

| Mission id | Prompt summary | Expected convergence |
|------------|----------------|----------------------|
| `pingsweep` | PS1 ping sweep + log file | Fix batch/PS syntax confusion (~2 iterations) |
| `python_missing_module` | Python script needing pip install | `host_exec` pip → `run_script` success |
| `ps1_backtick` | PS1 with LLM-style backtick bugs | Sanitizer + run |
| `fix_broken` | "Fix workspace/broken.ps1" | read → patch → run |
| `multi_file` | `.py` core + `.ps1` caller | Two deliverables, both run clean |
| `continue` | Resume mid-fix after turn cap | Plan rehydrates, fix loop completes |

---

## Regression commands

```powershell
.venv\Scripts\python.exe -m pytest tests/test_retry_executor.py -q
.venv\Scripts\python.exe -m pytest tests/test_task_plan.py -q
.venv\Scripts\python.exe -m pytest tests/test_ps1_sanitize.py -q
.venv\Scripts\python.exe tools_dev/validate_mission.py pingsweep
```

Full micro-agent suite target: all unit tests green + all golden missions pass live against configured Ollama model.

---

## Anti-regression rules

From `state/MEMORY.md` — do not regress:

- Parser must implement all documented fallback paths
- Text parser is mandatory when `native_tool_calls: 0`
- `run_script` for `.py` only; `.ps1` via `host_exec`
- Deliverable guard: never complete without file on disk
- Exit code 0 required for step done
