# Context Management, LLM Monitoring & Trial-and-Error Executor — 2026-06-10

Status: **DONE & VALIDATED** (live missions run 2026-06-10 against Ollama `qwen2.5-coder:7b-instruct`, sessions `20260610_015312` router / `20260610_015608` ping sweep).

Decisions: agent-side monitoring only; retries capped at 8/step then forced strategy change — never idle, never repeat the identical failing call.

## Phase 0 — LLM I/O observability

| Change | Where |
|--------|-------|
| Token telemetry: `prompt_eval_count`, `eval_count`, `total_duration_ms`, `ctx_saturation` (prompt tokens / `num_ctx`) per exchange | `agent.py` `OllamaAdapter.chat` → `core/debug_log.py::log_llm_interaction` |
| `native_tool_calls` vs `parsed_tool_calls` + parser fallback paths (`xml_tag`, `fenced_json`, `bare_json`, `inline_json`, `prose_json`, `code_block`) | `core/parser.py` (`last_discovery_paths`) |
| Audit modes `full` / `meta` / `off` via `config.yaml agent.llm_audit` (meta = roles/lengths/tokens only, avoids quadratic logs) | `core/debug_log.py`, `agent.py` |
| Viewer: `python tools_dev/llm_audit_view.py [--last N] [--session id] [--full]`; console `audit` → `llm` sub-view | `tools_dev/llm_audit_view.py`, `console.py::show_llm_audit` |

**Saturation rule:** `ctx_saturation >= 0.95` means Ollama silently truncated the input — reduce history window or injections.

## Phase 1 — Context diet

- LEAD pinned system: AGENTS + SOUL + one-line tool roster (`### TOOLS ###` markdown block removed — schemas arrive per turn via RELATED TOOL SCHEMAS).
- Specialists get a **minimal pinned prompt** (`PromptPack._assemble_specialist_minimal`): role line, tool-call format, bare tool names, scope rules. No AGENTS/SOUL/RAG during handoffs.
- Schema injection + Ollama `tools=` are reordered by the current plan step's `tool_hint` (`priority_tools` through `tool_schemas.py` / `context_router.py` / `_tools_schema_for_turn`).

## Phase 2 — Capped trial-and-error executor

- Verbatim error feedback (~1500 chars): deduplicated repeated lines, max 2 distinct error blocks, PowerShell `Line |` markers preserved (`ReActAgent._format_error_feedback`). Applied to `run_script` and `host_exec` (incl. non-terminating PS errors with exit 0).
- `TaskPlanTracker`: `attempt_counts` / `last_error_signatures` per step (persisted). `register_failure_attempt` →
  - same normalized error twice → `[SYSTEM — RETRY n/8]` change-approach nudge;
  - 8 attempts → step `BLOCKED` + `[SYSTEM — STRATEGY CHANGE REQUIRED]`; roadmap advances (BLOCKED is terminal for turn completion, excluded from readaptation loop).
- Success gating: a step is done only on `success != False` AND `exit_code in (0, None)`. `try_http_login` completes `attempt_login` only with a real verdict; transport errors fall to the retry path. Handoff completion (`success_exec`) also requires exit code 0.

## Phase 3 — Verbose content: artifact-first http_get (web auth Phases 1–2)

- `http_get` writes the FULL body to `state/sessions/<id>/artifacts/http_get_*.html` before truncation; returns `artifact_path`, `keyword_hits`, 2500-char preview.
- `facts.web.last_page` / `pages` in `[SESSION FACTS]` (url, artifact, keywords).
- `grep_file` / `read_file` shared with the web specialist (`SHARED_TOOLS` in `core/specialists.py`).
- `try_http_login` hard-gated until `fetch_page` done or artifact exists (`_fetch_before_login_error`).
- Playbook: `knowledge/tools/http_get.md`. Remaining: custom XML POST + `SID` cookie jar — [web_auth_html_pipeline_plan.md](./web_auth_html_pipeline_plan.md) Phase 3.

## Regression tests

```powershell
.venv\Scripts\python.exe tests/test_llm_audit.py
.venv\Scripts\python.exe tests/test_retry_executor.py
.venv\Scripts\python.exe tests/test_http_get_artifact.py
.venv\Scripts\python.exe -m pytest tests/ -q
```

Full suite: 296 passed. Known pre-existing environmental failure: `test_artifacts.py::test_find_file_prefers_output_reports` (accumulated `output/report_*.md` files push the knowledge playbook out of the capped match list — unrelated to this stack).

## Validation results (live, 2026-06-10, via `tools_dev/validate_mission.py`)

1. **Router login** (session `20260610_015312`): LEAD → delegate web → `http_get('http://192.168.1.1')` → 200 OK, **153,132-char body to artifact**, `facts.web.last_page` populated with `keyword_hits=[login, xmlobj, password, form, session, token]`; artifact greps 60 lines for `login|xmlobj|password|action=`. No premature `try_http_login` (gate active). ZTE `SID` cookie observed in headers (Phase 3 input).
2. **Ping sweep** (session `20260610_015608`, two turns): model first wrote a cmd.exe-style script (`@echo off`) → `run_script` rejected `.ps1` verbatim → `host_exec` returned the exact PowerShell parse error (Spanish locale, fed verbatim) → model removed `@echo off` and rewrote → corrected script exited 0 and `workspace/ping_results.log` contains live host output. Converged in 2 fix iterations, well under the 8-attempt cap; `[COMMAND FAILURE]`/`[SCRIPT FAILURE]` verbatim hints fired; RETRY/STRATEGY-CHANGE nudges never needed (error signature changed every attempt — correct).
3. **Token headroom**: worst `ctx_saturation` 0.742 (router) / 0.686 (sweep) at `num_ctx: 8192` — no input truncation. `native_tool_calls` confirmed 0 throughout (parser paths `bare_json`/`fenced_json` carried every call). `agent.llm_audit` switched to `meta` after validation.

**Live fix added during validation:** the 7B model reliably drops IP octets when re-typing URLs (`http://168.1.1` for `http://192.168.1.1`) and does not self-correct from timeout errors alone. Added `_correct_web_target_arg` (deterministic pre-dispatch URL substitution when the called host is a trailing fragment of the mission target) + `_build_web_target_hint` (post-failure correction nudge) in `agent.py`.
