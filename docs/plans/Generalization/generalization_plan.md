# Revised Cognitive Loop and Gating Generalization Plan

This document details the refined, robust, taxonomy-driven generalization plan for the Pulse Windows Agent cognitive loop. It integrates feedback to avoid brittle keyword matching, introduces clear intent policies, refines exhausted hash cracking status handling, and ensures robust, non-heuristic loop termination.

---

## 1. Core Structural Enhancements

### A. Intent Taxonomy & Soft Gating (`core/chat_goals.py`)
Rather than relying on fragile, keyword-based checks, we will define a clear **Intent Taxonomy** for incoming prompts:

1.  **`conversational`**: Direct natural-language dialogue, questions, and answers.
2.  **`coding` / `writing`**: Requests to write scripts, code, automation files, or documentation.
3.  **`analysis` / `review`**: Code reviews, log inspections, planning workflows, or security audits.
4.  **`network` / `retrieval`**: Host scanning, DNS lookups, ports probing.
5.  **`credential` / `pcap` / `forensic`**: Extraction of secrets, PCAP analysis, and decryption.

**Gating Policy Rule:**
*   **Hard Tool Gating** (`required_tools`) is **ONLY** attached to the `credential / pcap / forensic` category, or when the user explicitly names a specific tool to execute in their prompt.
*   For all other categories (`conversational`, `coding`, `analysis`, `network`), tool use is designated as **optional** or **disabled**. No hard gates will block turn completion.

---

### B. Dynamic Context Policies (`core/llm_utils.py`)
Instead of conditionally suppressing strings, we will modify the context builder to emit one of three explicit, testable, and clean **Dynamic Context Policies**:

1.  **`“plain answer allowed”`**: Emitted when the intent is purely conversational, planning-oriented, or for code-review. Instructs the LLM that it can respond with plain text and code blocks, and that no tool calls are expected.
2.  **`“tool use optional”`**: Emitted during general development, writing, or basic analysis where tools (like `read_file` or `run_script`) are available but not strictly required.
3.  **`“tool use required”`**: Emitted during active extraction/cracking tasks where a specific tool execution sequence is required before completion.

---

### C. `task_plan.py` & Bounded `exhausted` State
*   **Problem:** Mapping `exhausted` (hash search space completed with no password found) to `StepStatus.FAILED` creates plan readaptation loop stalls.
*   **Solution:** Introduce a new terminal state to the `StepStatus` enum: `PARTIAL` or `DONE_WITH_LIMITS`.
*   **Behavior:** When `crack_hash` finishes with `"status": "exhausted"`, the task step is marked as `StepStatus.PARTIAL` with a note `"Search space exhausted without match"`. This counts as a **bounded terminal state**, clearing the required task block and allowing `may_complete_turn()` to return `True` so the agent can finalize and write the deliverable output with the exhausted notice.

---

### D. Deterministic Loop Termination (`agent.py`)
*   **Problem:** Fuzzy heuristics like "contains detailed markdown blocks" are brittle.
*   **Solution:** Implement a strict, deterministic ReAct loop exit policy.
*   **Policy:** The ReAct loop exits immediately if:
    1.  The assistant's response contains **no tool calls**.
    2.  The assistant has produced a complete natural-language answer.
    3.  **No hard goals remain** (i.e. `chat_goals.pending(...)` is empty).
    No nudges (like `[SYSTEM] Task incomplete`) will be injected if these three conditions are met.

---

### E. Configuration-Driven multiline UX (`console.py`)
*   **Problem:** Hardcoding `Ctrl+Enter` bindings might fail or behave inconsistently depending on Windows terminal emulators (like older `cmd.exe`).
*   **Solution:**
    *   Expose a configuration key in `config.yaml` (e.g. `console.submit_binding: "ctrl-enter"`).
    *   Implement fallback logic in `console.py`: if the environment/terminal does not support the binding, print a warning and fall back gracefully to standard multiline submission behavior (`Enter` + multiline editing, with `Esc+Enter` or default submit).

---

## 2. Updated Testing Strategy

To ensure this generalization does not regress, we will write focused automated tests in `tests/`:

1.  **`test_false_positive_gating`**:
    *   **Input:** "review this Python script for security issues" or "write a Python script to connect to an API".
    *   **Assertion:** Verify that the detected intent taxonomy falls into `analysis` / `coding`, **no rigid required tool is injected** (e.g., `find_and_grep` remains un-mandated), and no loop nudge is emitted.
2.  **`test_true_negative_gating`**:
    *   **Input:** "extract the password from last_capture.pcapng" or "crack this target hash".
    *   **Assertion:** Verify that the detected intent is `credential` / `pcap` / `forensic`, the hard gate is successfully attached, and tools like `analyze_pcapng` or `crack_hash` are strictly enforced.
3.  **`test_exhausted_partial_status`**:
    *   **Assertion:** Verify that `crack_hash` returning `exhausted` maps the task step to `PARTIAL`/`DONE_WITH_LIMITS`, allows the turn to complete successfully, and permits writing `pwd.txt`.
