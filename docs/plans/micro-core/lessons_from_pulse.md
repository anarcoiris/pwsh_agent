# Lessons from Pulse — For a Script-Builder Micro Agent

**Status:** PROPOSAL  
**Date:** 2026-06-10

Distilled from Pulse (`pwsh_agent`) milestones, validation runs, and known failure modes. A micro agent scoped to **PowerShell + Python script building and testing** should be **narrow on domain** but **strict on structure**.

---

## What this project got right (reuse directly)

### 1. Formalize the order before any code

The biggest lesson from `multi_purpose_agent_design.md` is: **intent drives everything**. Pulse moved from regex-only routing (which sent "login with password" to `crack_hash`) to `IntentSpec`:

- `summary`, `domain`, `objectives`, `deliverables`, `constraints`, `success_criteria`, `capabilities`

For a script-builder micro agent, every order should become something like:

```json
{
  "domain": "scripting",
  "deliverables": ["workspace/ping_sweep.ps1"],
  "objectives": [
    "acknowledge requirements",
    "scaffold script at deliverable path",
    "run with correct runner",
    "fix until exit 0 and output artifact exists"
  ],
  "success_criteria": [
    "deliverable file exists on disk",
    "script exits 0 when executed",
    "workspace/ping_results.log contains expected output"
  ],
  "constraints": ["powershell and python only", "use project venv for .py"]
}
```

**Acknowledging the order** is not polite fluff — it is persisting this spec and echoing it back in `CURRENT STATE` each turn so the model cannot drift.

### 2. Atomic plans with explicit step status

`TaskPlanTracker` (`core/task_plan.py`) is the right mental model:

| Step | Capability | Tool hint | Done when |
|------|------------|-----------|-----------|
| `ack` | conversation | — | spec written + user restated |
| `scaffold` | file_write | `write_file` | deliverable path exists |
| `run` | scripting | `run_script` / `host_exec` | executed once |
| `fix` | scripting | read + rewrite + rerun | exit 0 |
| `verify` | file_read | `read_file` / grep | success_criteria met |

Statuses (`PENDING → IN_PROGRESS → DONE | FAILED | BLOCKED`) and **exit-code gating** (step not done unless `exit_code == 0`) were validated live on the ping-sweep mission — essential for a builder agent.

### 3. Build → run → iterate with capped retries

The **2026-06-10 retry stack** (`docs/plans/context_retry_stack_20260610.md`) is the blueprint for iteration:

- Verbatim stderr (~1500 chars), deduped, with PowerShell `Line |` markers preserved
- Per-step attempt counts + normalized error signatures
- Same error twice → "change approach" nudge
- 8 attempts → `BLOCKED` + forced strategy change (never infinite loops)

The ping-sweep validation is the canonical success story: wrong batch syntax (`@echo off` in `.ps1`) → verbatim Spanish PowerShell error → fix → exit 0 in **2 iterations**.

### 4. Correct runner routing (non-negotiable)

From `knowledge/tools/run_script.md` and live failures:

| Language | Write | Run | Never |
|----------|-------|-----|-------|
| `.py` | `write_file` | `run_script` (venv python) | `powershell -File script.py` |
| `.ps1` | `write_file` (+ sanitizer) | `host_exec` | `run_script` for `.ps1` |

A micro agent should **hard-enforce** this at dispatch time, not rely on the model.

### 5. Deliverable guards beat "I saved it" hallucinations

Pulse learned that models claim success without files on disk. Keep:

- `TaskIntentExtractor` for `.py` / `.ps1` paths from the user message
- `WriteGuard` blocking progress notes to wrong paths
- `pending_deliverables()` checks before turn completion
- Code-block parser path: fenced ` ```python` / ` ```powershell` → `write_file`

### 6. Minimal context for the worker loop

Specialists got a **minimal pinned prompt** (role + tool format + scope) — no full AGENTS/SOUL/RAG dump. A micro agent doing only script work should use an even smaller pack:

- Script conventions (PS vs PY runners)
- Error interpretation rules
- Current step + last stderr
- Required deliverable paths

This kept `ctx_saturation` under ~0.75 in validation; the micro agent should target the same.

### 7. Golden missions as regression harness

`tools_dev/validate_mission.py` with missions like `pingsweep` is the right pattern:

```text
Write ping_sweep.ps1 → run with host_exec → fix until ping_results.log is correct
```

The micro agent should ship 5–10 such missions and run them against live Ollama in CI or nightly.

---

## What hurt this project (design the micro agent to avoid)

### 1. Domain bleed from a generalist agent

Pulse's "one trick" (PCAP/hash) leaked into unrelated tasks via five layers of regex defaults. A script micro agent should **not** load recon/web/crypto tools or playbooks at all — narrow scope is protection.

### 2. Regex-only planning for dev tasks

Today, scripting plans are thin — a single `write_file` step unless domain-specific logic fires (`core/task_plan.py` ~L191–196). That is not enough for "build, test, iterate." The micro agent needs a **registered scripting plan template**:

1. Parse requirements → restate
2. Write skeleton
3. Run
4. Fix loop (sub-task per error class)
5. Verify outputs
6. Report

### 3. Premature completion and handoff confusion

Session closure (2026-06-04) showed specialists completing handoffs on the wrong tool (`append_note` instead of actual work). For a single-purpose builder:

- **No LEAD/workspace handoff** unless you truly need orchestrator + coder separation
- Completion only when **file exists + run succeeded + optional output checks**

### 4. Turn/step budget too low for fix loops

The ping-sweep needed a **`continue`** mission because chat mode capped at 12 steps mid-fix. A builder micro agent should either:

- Use a **mission loop** (`max_steps: 30`) for build-fix tasks, or
- Auto-continue while current step is `IN_PROGRESS` and attempts < cap

### 5. Small-model fragility

Validated issues with `qwen2.5-coder:7b`:

- `native_tool_calls: 0` → text parser is mandatory
- Drops IP octets when retyping URLs (less relevant for scripting, but same class of "retype errors")
- Embeds bad PS syntax in JSON (fixed by `_sanitize_powershell_content`)

Plan for **deterministic pre/post-processing**, not "smarter prompts" alone.

### 6. Stale plan persistence across unrelated messages

Pulse fixed domain-mismatch rehydration (`load_plan_state` discards plans when domain changes). The micro agent should scope plans to **one mission id** and discard on new order unless explicitly "continue."

---

## Validation evidence (Pulse, 2026-06-10)

| Mission | Outcome |
|---------|---------|
| Router login | Fetch → 153KB artifact → gated login; no premature `try_http_login` |
| Ping sweep | Converged through `@echo off` batch/PS confusion in 2 fix iterations; exit 0 |
| Context headroom | Worst `ctx_saturation` 0.742 < 0.95 — no truncation |

See `memory/2026-06-10.md` and `docs/plans/context_retry_stack_20260610.md` for full details.
