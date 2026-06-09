# Session closure — 2026-06-04

Status: **CLOSED** (implementation complete for specialist/session fixes; web-auth HTML pipeline deferred).

Sessions exercised: `20260603_232001`, `20260603_233207`, `20260603_234404`  
Primary task: ZTE router login at `http://192.168.1.1` (`web_auth` domain).

---

## Problems found and fixed (verified)

### 1. Tool schema truncation (`H1`)

**Symptom:** Web agent could not call `try_http_login`; LEAD could not see `delegate_to` in Ollama `tools=` API.

**Cause:** `prompt_pack.schemas_for_agent(max_chars=2400)` dropped tools alphabetically; `find_tshark` missing from `TOOLS_SCHEMA`.

**Fix:** `core/tool_schemas.py` — priority ordering, 8000-char budget, shared by `prompt_pack` and `context_router`. Added `find_tshark` schema.

**Tests:** `tests/test_tool_schemas.py`

---

### 2. Premature handoff via `append_note` (`H2`)

**Symptom:** After `delegate_to(web)`, specialist called `append_note` → handoff marked complete without `http_get`.

**Cause:** `specialist_soft_scope` allowed LEAD-only tools; any successful specialist tool closed handoff.

**Fix:** `LEAD_ONLY_TOOLS` hard-blocked for specialists; handoff completes only when `tool_allowed(active_agent, tool_name)`.

**Tests:** `tests/test_specialist_handoff.py`

---

### 3. Specialist stall after delegate (`H3`)

**Symptom:** Model retried blocked `append_note` / `delegate_to` instead of `http_get`.

**Fix:** Post-delegate action nudge; bootstrap after 2+ LEAD-only blocks; fetch-before-login ordering in bootstrap.

**Modules:** `core/specialists.py` (`specialist_action_nudge`, `extract_target_url`), `agent.py` (`_bootstrap_specialist_action`).

---

### 4. Orphan `(WEB)` console badge (`H4`)

**Symptom:** `active_agent=web` stuck in RAM after incomplete turn; `session clear` did not reset.

**Cause:** Handoff state is in-memory only; `session clear` only cleared prior handoff pick.

**Fix:** `_reset_orphan_specialist` at chat turn start/end; `reset_handoff_to_lead()`; `session clear` resets specialist to LEAD.

**Tests:** `tests/test_orphan_specialist.py`

---

### 5. CURRENT_STATE confusion (documentation)

**Clarified:** `state/sessions/<id>/CURRENT_STATE.md` is **audit/replay only**. Each turn rebuilds state via `build_current_state()` from RAM + `working_memory.json` + `plan_state.json` + `facts.json`. The LLM never reads the file back.

---

## Router login outcome (expected limitation)

Bootstrap ran `try_http_login` with basic + generic form POST. ZTE returned 200 with failure markers — credentials likely rejected because login is **XML/JS-encoded**, not a plain HTML form.

**Next work:** See [web_auth_html_pipeline_plan.md](./web_auth_html_pipeline_plan.md).

---

## Regression commands

```powershell
python tests/test_tool_schemas.py
python tests/test_specialist_handoff.py
python tests/test_delegate_to.py
python tests/test_orphan_specialist.py
python tests/test_prompt_pack_budgets.py
python tests/test_prompt_pack_injection.py
```

---

## Console operator notes

| Command | Effect |
|---------|--------|
| `new` | New session id; seals prior handoff; resets specialist to LEAD |
| `session list` | Active id + sealed handoff summaries (historical, not deleted by clear) |
| `session pick <id>` | Inject prior handoff into CURRENT STATE |
| `session clear` | Drop pick + reset specialist to LEAD (active session unchanged) |

---

## Key files (this session)

| Module | Role |
|--------|------|
| `core/tool_schemas.py` | Per-agent Ollama schema selection (no truncation gaps) |
| `core/specialists.py` | Registry, `LEAD_ONLY_TOOLS`, nudge helpers, `delegate_to` |
| `core/working_state.py` | `build_current_state`, audit-only `CURRENT_STATE.md` |
| `core/prompt_pack.py` | 4-file contract; delegates schemas to `tool_schemas` |
| `core/context_router.py` | Pack-mode schema injection |
| `agent.py` | Handoff guards, bootstrap, orphan reset |
| `console.py` | Session clear messaging |

---

## Open follow-ups (not in this closure)

1. Web auth HTML/XML pipeline — [web_auth_html_pipeline_plan.md](./web_auth_html_pipeline_plan.md)
2. Batch note + strict artifact compaction — [implementation_plan.md](./implementation_plan.md) (pre-existing, not started)
3. ZTE-specific login POST (custom XML body) — part of web auth plan Phase 2
