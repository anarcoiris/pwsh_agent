# Analysis and Generalization Plan for Pulse Agent Console & Cognitive Loop

> **STATUS (2026-06): IMPLEMENTED / SUPERSEDED.** The fixes here (exhausted-crack
> PARTIAL state, soft chat goals, GENERAL phase, Ctrl+Enter submit binding) have
> shipped. Ongoing generalization work lives in
> `../Generalization/multi_purpose_agent_design.md`. Kept for historical context.

This document outlines the findings, root causes, and architectural proposals to resolve the loop stalls, repeated completions, and too-specific behaviors observed in the interactive ReAct loop of the Pulse Windows Agent.

---

## 1. Core Findings & Root Causes

### Finding 1: Conversation Hijacking via Over-Specific Goal Detection
* **Root Cause:** `core/chat_goals.py` uses a central `ChatGoalRegistry` that matches incoming user prompts or session history against highly specific, network-centric regex filters.
* **The Bug:** If the user mentions words like `login` or `password`, or if the session has prior packet capture work, the registry matches the prompt as `_build_credential_session_goal` (Credential extract follow-up).
* **The Consequence:** This goal immediately mandates the `find_and_grep` tool as a `required_tool`. Even when the user simply asks: *"plan a way (or a couple options) to login..."* (which is conversational/planning) or *"write a Python script to try and login"*, the agent is **blocked** from responding directly. Instead, it is forced to execute `find_and_grep` and `read_file` on PCAP logs, which degrades the UX and results in irrelevant actions.

### Finding 2: Directives & Nudges Forcing Tool Use on Conversational Intent
* **Root Cause:** `core/llm_utils.py` and `agent.py` enforce strict nudges when a user turn concludes without tool execution:
  * `"Do NOT emit [SYSTEM] Task complete or **Next Steps** prose — emit a <tool_call> block."`
  * `"[SYSTEM] Task incomplete — emit a tool call instead of summarizing."`
* **The Bug:** When the user prompt is purely conversational (e.g., asking to plan options, review a script, or explain a finding), there is no logical tool to call.
* **The Consequence:** The LLM gets trapped. It tries to output prose (such as `[SYSTEM] Task complete.`), but the ReAct loop in `agent.py` sees "no tools were executed," appends a system nudge, and forces another LLM call. This results in the repeated `🧠 Reasoning: [SYSTEM] Task complete.` logs observed in the console transcript, where the LLM repeats itself up to 12 times (the step budget limit) until the loop finally breaks.

### Finding 3: Exhausted-Crack Step Failure in the Roadmap Tracker
* **Root Cause:** `core/task_plan.py` explicitly marks `crack_hash` outcomes as `StepStatus.FAILED` if the hash is not cracked:
  ```python
  if result.get("success") is False or result.get("status") == "exhausted":
      s.status = StepStatus.FAILED
  ```
* **The Bug:** A terminal `exhausted` result means the entire search space was searched and no candidate matched—this is a **completed action**, not a tool or execution failure.
* **The Consequence:** By marking the step `FAILED`, `task_plan.py` triggers `needs_readaptation() = True`, which prevents `may_complete_turn()` from returning `True` in `agent.py`. This traps the agent in a loop of trying to repeat or readapt `crack_hash` instead of accepting the result, writing `pwd.txt` with `NOT CRACKED (exhausted)` status, and finishing.

---

## 2. Proposed Generalization & Fix Ideas

### Fix 1: Generalize the ChatGoal System
* **Goal:** Allow the agent to dynamically switch between network/credential extraction tasks and general coding, code-reviewing, or conversational tasks.
* **Solution:** 
  1. **Dynamic Dev/Chat Goal Skip:** If a prompt contains coding keywords (e.g. `python`, `script`, `powershell`, `write`, `plan`, `options`, `review`) or lacks explicit PCAP targets, bypass the over-specific `required_tools` requirements.
  2. **Soft Goals:** Convert rigid goal checks into soft hints. Rather than hard-blocking a turn if `find_and_grep` isn't called, treat it as a suggestion that the LLM may ignore if the user's intent is conversational or code-oriented.
  3. **Refine Session Follow-ups:** In `_build_credential_session_goal`, check if the prompt is purely planning or scripting-oriented before mandating `find_and_grep`.

### Fix 2: Reliable & Deterministic Stop Points
* **Goal:** Stop the ReAct loop immediately when the LLM outputs a conversational response, a plan, or code, rather than nudging it back into tool execution.
* **Solution:**
  1. **Acknowledge Conversational Intent:** If the LLM generates a response with *no* tool calls and `step >= 0`, inspect the text. If the text contains detailed markdown lists, explanations, or code blocks matching the user's planning request, immediately treat the turn as **DONE** and break the ReAct loop.
  2. **Clean Completion Signaling:** Allow the LLM to signal task completion naturally in prose (e.g., using `Task complete` or writing standard markdown summaries) when there are no more tools left to run.
  3. **Fix the 1-Step Tool Expectation:** Modify `agent.py` so it doesn't nudge with `[SYSTEM] No tools executed yet` if the prompt does not warrant tool execution (e.g. simple questions or planning requests).

### Fix 3: Standardize the `exhausted` Crack State
* **Goal:** Let the roadmap progress naturally when a hash cannot be cracked.
* **Solution:**
  1. Update `core/task_plan.py` so that if `result.get("status") == "exhausted"`, the step is set to `StepStatus.DONE` with a note `"Hash space exhausted (not cracked)"`.
  2. Ensure `write_file` is allowed to proceed to write the final deliverable showing the exhausted status.

---

## 3. Implementation Plan

### Track A: Correctness & Generalization
1. **Fix `core/task_plan.py`**:
   * Change `exhausted` hash crack status mapping to `StepStatus.DONE` (or specific terminal non-failure state).
2. **Soften Chat Goals (`core/chat_goals.py`)**:
   * Add a heuristic check: if the user's prompt is a planning, review, or scripting request, bypass standard required_tools checks.
   * Make `required_tools` soft. If the LLM produces a rich conversational response or plan without calling the requested tools, do not force/bootstrap the execution.
3. **Robust Loop Termination (`agent.py`)**:
   * Break the cognitive loop immediately if the LLM produces a conversational response with no tool calls when all hard goals are satisfied, or if the prompt is a planning request.
   * Stop emitting loop nudges when the LLM signals completion.

### Track B: Console & UX Polish
1. **Regex Salt Hint Hardening (`core/tool_hints.py`)**:
   * Exclude connector words like `with`, `from`, `before`, etc., from fallback salt capture.
2. **Multiline UX (`console.py`)**:
   * Add standard `Ctrl+Enter` submit key binding in `prompt_toolkit`.
3. **Compact Event IDs & `/show` Commands (`console.py`)**:
   * Assign compact panel IDs to tool calls/results and support `/show` command review.
