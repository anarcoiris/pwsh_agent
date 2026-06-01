# Implementation Plan - Generalizing the Cognitive Loop and Fixing Console Loops

This plan details the technical changes required to resolve cognitive loop stalls (e.g. repeated `[SYSTEM] Task complete` or loop nudges) and generalize the agent's task-gating logic so it can cleanly handle non-network tasks (such as coding, reviews, and general conversation).

## User Review Required

> [!IMPORTANT]
> The current system has strong required-tool rules (such as forcing `find_and_grep` or `analyze_pcapng` if keywords like `login`, `password`, or `pcap` are present). This plan will soften those constraints using a structured intent taxonomy to allow conversational, code-writing, and review tasks to succeed immediately without redundant tool execution.

## Proposed Changes

We will modify five key files in the codebase to soften the goal-gating registry, resolve loop termination bugs, and polish console interaction.

---

### [Component 1] Task Plan & State Gating (`core/task_plan.py`)

#### [MODIFY] [task_plan.py](file:///c:/Users/soyko/Documents/pwsh_agent/core/task_plan.py)
* **Goal:** Correctly handle `exhausted` hash crack statuses as terminal completions, not failures.
* **Changes:**
  * Add a new state `PARTIAL = "partial"` or `DONE_WITH_LIMITS` to the `StepStatus` enum at `L13`.
  * Update `register_tool` at `L192` so that if `tool_name == "crack_hash"` and `result.get("status") == "exhausted"`, the corresponding `TaskStep` status is marked as `StepStatus.PARTIAL` with a note `"Search space exhausted without match"` instead of `StepStatus.FAILED`.
  * Ensure `needs_readaptation()` returns `False` when a step is in `StepStatus.PARTIAL`, and `may_complete_turn()` treats `StepStatus.PARTIAL` as a valid bounded terminal state to let the turn complete.

---

### [Component 2] Chat Goal Registry (`core/chat_goals.py`)

#### [MODIFY] [chat_goals.py](file:///c:/Users/soyko/Documents/pwsh_agent/core/chat_goals.py)
* **Goal:** Generalize task goals using a structured intent taxonomy.
* **Changes:**
  * Define a clean **Intent Taxonomy**:
    1.  `conversational`: Direct natural-language dialogue, questions, and answers.
    2.  `coding` / `writing`: Requests to write scripts, code, automation files, or documentation.
    3.  `analysis` / `review`: Code reviews, log inspections, planning workflows, or security audits.
    4.  `network` / `retrieval`: Host scanning, DNS lookups, ports probing.
    5.  `credential` / `pcap` / `forensic`: Extraction of secrets, PCAP analysis, and decryption.
  * In `ChatGoalRegistry` and goal matchers, classify the user prompt.
  * **Only attach hard tool requirements** (`required_tools`) if the detected intent is `credential / pcap / forensic`, or if the user explicitly names a specific tool to execute in their prompt.
  * For other taxonomy intents (e.g. `conversational`, `coding`, `analysis`), tool use is designated as **optional** or **disabled**. No hard gates will block turn completion.

---

### [Component 3] Dynamic Context Directives (`core/llm_utils.py`)

#### [MODIFY] [llm_utils.py](file:///c:/Users/soyko/Documents/pwsh_agent/core/llm_utils.py)
* **Goal:** Prevent loop nudges using three explicit context policies.
* **Changes:**
  * In `DynamicContextBuilder.build_context`, detect the taxonomy intent and return one of three explicit, testable policies:
    1.  `“plain answer allowed”`: Emitted when the intent is conversational, planning-oriented, or code-review. Instructs the LLM that it can respond with plain text and code blocks, and that no tool calls are expected.
    2.  `“tool use optional”`: Emitted during general development, writing, or basic analysis where tools are available but not strictly required.
    3.  `“tool use required”`: Emitted during active extraction/cracking tasks where a specific tool execution sequence is required before completion.

---

### [Component 4] ReAct Cognitive Loop (`agent.py`)

#### [MODIFY] [agent.py](file:///c:/Users/soyko/Documents/pwsh_agent/agent.py)
* **Goal:** Establish a robust, non-heuristic loop stop condition.
* **Changes:**
  * In `chat_turn`, implement a strict loop exit policy: immediately break the ReAct loop and return the response if:
    1.  The assistant's response contains **no tool calls**.
    2.  The assistant has produced a complete natural-language answer.
    3.  **No hard goals remain** (i.e. `chat_goals.pending(...)` is empty).
  * Prevent injecting loop nudges (such as `[SYSTEM] No tools executed yet` or `[SYSTEM] Task incomplete — emit a tool call`) when these conditions are met.

---

### [Component 5] Console multiline UX (`console.py`)

#### [MODIFY] [console.py](file:///c:/Users/soyko/Documents/pwsh_agent/console.py)
* **Goal:** Configurable multiline submission behavior.
* **Changes:**
  * Expose a configuration key in `config.yaml` (e.g. `console.submit_binding: "ctrl-enter"`).
  * Expose this configuration in `AgentConsole.run_repl`. Set up `prompt_toolkit` custom key bindings so `Ctrl+Enter` is configured only if enabled and supported, falling back gracefully to standard multiline submission behavior (`Enter` + multiline editing, with `Esc+Enter` or default submit).

---

## Verification Plan

### Automated Tests
* Run existing test suites (`pytest tests/`) to ensure no regressions in basic behaviors:
  ```powershell
  pytest tests/test_task_plan.py
  pytest tests/test_chat_goals.py
  pytest tests/test_tool_hints.py
  ```
* Write new tests:
  * **`tests/test_conversational_gating.py`**:
    * Test 1 (False-positive check): Asserts that prompts like `"review this Python script"` or `"write a script"` are classified into the `analysis` / `coding` taxonomies, **no rigid required tool is injected**, and no loop nudge is emitted.
    * Test 2 (True-negative check): Asserts that prompts like `"extract password from last_capture.pcapng"` are classified as `credential / pcap / forensic` and get the hard gate successfully.
  * **`tests/test_exhausted_partial_status.py`**:
    * Asserts that `crack_hash` returning `exhausted` maps the task step to `PARTIAL` status, allows the turn to complete successfully, and permits writing `pwd.txt`.

### Manual Verification
* Run the interactive console via `python console.py`.
* Converse with the agent with non-network/conversational tasks (e.g. asking to plan options, review code, or write a general script). Ensure it responds immediately without loops, multiple `[SYSTEM] Task complete` statements, or forced `find_and_grep` tool calls.
