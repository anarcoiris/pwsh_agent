# Implementation Plan: Batch Note Updating & Strict Context Compaction

> **Status:** DONE. Specialist handoff and prompt pack work completed 2026-06-04 — see [session_closure_20260604.md](./session_closure_20260604.md) and [specialist_handoff_plan.md](./specialist_handoff_plan.md). Web auth HTML pipeline — [web_auth_html_pipeline_plan.md](./web_auth_html_pipeline_plan.md).

This plan outlines the architecture for the two accepted proposals from the earlier audit analysis.

## Proposal 1: Batch Note Updating (Multi-Tool Use)

**Goal:** Allow the agent to perform administrative "busy work" (updating plan, status, scratchpads) and execute an action in a single LLM turn, collapsing 45s consecutive turns into one.

### 1. `core/parser.py`
#### [MODIFY] [parser.py](file:///c:/Users/soyko/Documents/pwsh_agent/core/parser.py)
- Refactor `_pick_best_tool_calls` to separate `append_note` tools from "action" tools.
- Instead of rigidly capping total extracted tools to `limit=1`, we will return **ALL** valid `append_note` calls plus at most **ONE** highest-priority action tool. 
- This safely leverages `ReActAgent`'s existing `for tc in tool_calls:` execution loop without risking sequential action failures (like running a broken script and then blindly piping its output).

### 2. `agent.py` (System Prompt)
#### [MODIFY] [agent.py](file:///c:/Users/soyko/Documents/pwsh_agent/agent.py)
- Update the ReAct Cognitive Workflow prompt. 
- Change "Emit ONE tool call per turn" to "Emit ONE ACTION tool call per turn. You may additionally include multiple `<tool_call>` blocks for `append_note` in the same turn to update your notes simultaneously."

---

## Proposal 2: Strict Context Compaction for Artifacts

**Goal:** Aggressively truncate heavy tool outputs (like raw HTML from `http_get` or `read_file`) in *older* turns to prevent KV cache bloat, while leaving the *most recent* turn untouched so the agent can still analyze the data it just fetched.

### 1. `core/context.py`
#### [MODIFY] [context.py](file:///c:/Users/soyko/Documents/pwsh_agent/core/context.py)
- Introduce a strict per-message char cap (e.g., `1000` chars) specifically for heavy artifact tools: `read_file`, `http_get`, `host_exec`, and `capture_packets`.
- Modify `_apply_per_message_caps(messages, max_tool_chars, per_message_cap)` to become turn-aware, or modify `trim_context()` to apply this strict cap *only* to messages that belong to older turns (i.e., not the most recent assistant/tool turn).
- Older heavy tool results will be truncated with a clear marker: `\n[... aggressively truncated older artifact ...]`.

## Verification Plan

### Automated/Manual Testing
1. **Batch Notes:** 
   - We will run the unit tests for the parser to ensure it correctly extracts and returns multiple `append_note` calls alongside a single action tool.
   - We can add a quick test in `tests/test_parser.py` (if it exists) or test manually via the agent.
2. **Strict Compaction:**
   - We will verify that after a heavy `http_get` call, the tool's output is full in the immediate next turn, but truncated in the turn after that. 
   - Ensure the context token count remains stable during extended reconnaissance.

## Open Questions
- Is a `1000` character limit for older heavy artifacts sufficient to preserve continuity without bloating the prompt? (I believe 1000 is generous enough for trailing logs but small enough to save ~10k+ tokens).
