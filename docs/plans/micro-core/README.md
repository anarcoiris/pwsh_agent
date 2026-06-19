# Micro-Core — Script Builder Micro Agent

**Status:** PROPOSAL (2026-06-10)  
**Scope:** PowerShell (`.ps1`) and Python (`.py`) only — build, test, iterate.

Design brief for a narrow-purpose micro agent that acknowledges orders, splits work into atomic missions/plans/tasks, then builds scripts, runs them, and converges on success. Grounded in lessons from the Pulse agent (`pwsh_agent`) achievements and failures.

## Documents

| Document | Summary |
|----------|---------|
| [script_builder_design_brief.md](./script_builder_design_brief.md) | Full synthesis — goals, architecture, completion criteria, summary |
| [lessons_from_pulse.md](./lessons_from_pulse.md) | What to reuse vs what to avoid from this repo |
| [architecture.md](./architecture.md) | Layer model, tool surface, orchestration, config sketch |
| [testing_strategy.md](./testing_strategy.md) | Unit, golden intents, live missions, telemetry |

## Related Pulse docs

- [context_retry_stack_20260610.md](../context_retry_stack_20260610.md) — capped trial-and-error executor (validated live)
- [Generalization/multi_purpose_agent_design.md](../Generalization/multi_purpose_agent_design.md) — IntentSpec, capability registry, generic planner
- [session_closure_20260604.md](../session_closure_20260604.md) — specialist handoff pitfalls
- [../agent-loop.md](../agent-loop.md) — shared ReAct loop architecture

## Key code references

| Module | Role |
|--------|------|
| `core/intent_spec.py` | Intent formalization (`IntentSpec`) |
| `core/task_plan.py` | Atomic steps, retry cap, exit-code gating |
| `core/task_intent.py` | Deliverable extraction (`.py`, `.ps1`) |
| `core/write_guard.py` | Deliverable path enforcement |
| `tools_dev/validate_mission.py` | Live mission harness pattern |
| `knowledge/tools/run_script.md` | Python vs PowerShell runner routing |
