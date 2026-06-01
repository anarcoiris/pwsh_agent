# Consolidated Generalization Plan — Pulse Windows Agent

> **STATUS (2026-06): IMPLEMENTED / SUPERSEDED.** The defensive de-biasing in this
> document (PARTIAL crack state, dev-intent gating, GENERAL phase branch,
> configurable console submit binding) has shipped. The constructive successor —
> intent formalization, capability registry, generic planner, soft-guidance loop —
> is tracked in `multi_purpose_agent_design.md`. Kept for historical context.

**Source:** Cherry-picked from `Original_proposal.md` + `generalization_plan.md`, grounded in code review of `agent.py`, `core/chat_goals.py`, `core/task_plan.py`, `core/llm_utils.py`, and `console.py`.

> **Important behavioral change:** The current system enforces hard required-tool rules (e.g. forcing `find_and_grep` or `analyze_pcapng` when keywords like `login`, `password`, or `pcap` appear). This plan softens those constraints using intent detection so that conversational, coding, and review tasks can complete immediately without forced tool execution.

---

## Implementation Order (by risk, lowest first)

| # | Component | File | Risk | Effort |
|---|-----------|------|------|--------|
| 1 | `exhausted` crack state | `core/task_plan.py` | Low | ~10 lines |
| 2 | Step-0 nudge gate | `agent.py` | Low | ~5 lines |
| 3 | General phase directive | `core/llm_utils.py` | Low | ~8 lines |
| 4 | False-positive gating | `core/chat_goals.py` | Medium | ~30 lines |
| 5 | Configurable console UX | `console.py` + `config.yaml` | Low | ~20 lines |

---

## Component 1 — Task Plan & Bounded `exhausted` State (`core/task_plan.py`)

### Goal
Correctly handle `exhausted` hash crack status as a bounded terminal completion, not a failure that triggers readaptation loops.

### Root cause in code
`register_tool()` maps both `success is False` and `status == "exhausted"` to `StepStatus.FAILED`:

```python
# task_plan.py — current buggy code (~L192)
if tool_name == "crack_hash" and isinstance(result, dict):
    if result.get("success") is False or result.get("status") == "exhausted":
        s.status = StepStatus.FAILED   # ← exhausted wrongly becomes a failure
```

**Additional inconsistency:** `agent.py:_execute_tool()` already treats `exhausted` as `success_flag = True` for ChatGoals tracking, but `task_plan.py` still marks the step `FAILED`. The two modules are out of sync.

### Changes

**1a. Add `PARTIAL` to `StepStatus` enum (~L13):**
```python
class StepStatus(str, Enum):
    PENDING        = "pending"
    IN_PROGRESS    = "in_progress"
    DONE           = "done"
    FAILED         = "failed"
    SKIPPED        = "skipped"
    PARTIAL        = "partial"   # ← ADD: bounded terminal (e.g. search space exhausted)
```

**1b. Update `register_tool()` to split `exhausted` from genuine failures (~L192):**
```python
if tool_name == "crack_hash" and isinstance(result, dict):
    if result.get("status") == "exhausted":
        # Search space fully explored — bounded terminal, not a failure
        for s in self.steps:
            if s.id == "crack_hash":
                s.status = StepStatus.PARTIAL
                s.note = "Search space exhausted without match"
        return
    if result.get("success") is False:
        # Genuine tool error (bad args, launcher missing)
        self.last_failure = str(result.get("error") or result.get("stderr") or "crack_hash failed")
        for s in self.steps:
            if s.id == "crack_hash":
                s.status = StepStatus.FAILED
                s.note = self.last_failure[:200]
        return
```

**1c. Guard `needs_readaptation()` to exclude PARTIAL:**
```python
def needs_readaptation(self) -> bool:
    return any(s.status == StepStatus.FAILED for s in self.steps)
    # PARTIAL is intentionally excluded — it is a valid terminal outcome
```

**1d. Allow `may_complete_turn()` to proceed when all steps are DONE or PARTIAL:**
```python
def may_complete_turn(self, tools_executed: list[str], step_index: int, min_steps: int = 2) -> bool:
    if not self.steps:
        return step_index >= min_steps
    if self.needs_readaptation():
        return False
    terminal = {StepStatus.DONE, StepStatus.PARTIAL, StepStatus.SKIPPED}
    if not all(s.status in terminal for s in self.steps):
        return False
    return step_index >= min_steps
```

### Tests
- **New:** `tests/test_exhausted_partial_status.py` — assert `crack_hash` returning `{"status": "exhausted"}` maps the step to `StepStatus.PARTIAL`, `needs_readaptation()` returns `False`, `may_complete_turn()` returns `True` at `step_index >= 2`, and `write_file` on `pwd.txt` is not blocked.
- **Regression:** `pytest tests/test_task_plan.py` — existing placeholder and credential tests must still pass.

---

## Component 2 — Step-0 Nudge Gate (`agent.py`)

### Goal
Stop forcing tool execution on step 0 when no chat goals are active (conversational, planning, or code-review prompts).

### Root cause in code
In `chat_turn()`, the following nudge fires unconditionally for any turn where no tool was called on step 0:

```python
# agent.py — chat_turn(), in the no-tool-call branch
if step < 1 and not tools_executed_names:
    self._add_nudge(
        "[SYSTEM] No tools executed yet. Call an appropriate tool "
        "before summarizing."
    )
    continue
```

When `chat_goals` is `None` (which is correct for conversational/coding tasks), there are no required tools and the LLM's plain-text response is valid — but this nudge still fires and pushes it back into the loop.

### Change

Add a `chat_goals` guard before the nudge:

```python
# agent.py — replace the unconditional nudge with:
if step < 1 and not tools_executed_names:
    if chat_goals:
        # Active goal with required tools — nudge is valid
        self._add_nudge(
            "[SYSTEM] No tools executed yet. Call an appropriate tool "
            "before summarizing."
        )
        continue
    else:
        # No active goals → conversational/planning response is complete
        break
```

This also requires checking the `pending_now` path in the same branch. When `chat_goals is None`, `pending_now` will be `[]`, so the existing `if pending_now:` guard already skips the nudge there. The step-0 guard above is the only unconditional path that needs fixing.

### Tests
- **New:** `tests/test_false_positive_gating.py`, Test 1 — feed `"review this Python script for security issues"` and `"write a Python script to connect to an API"` through the intent detection; assert `ChatGoalRegistry.match_message()` returns `None` and no nudge fires.
- **Regression:** `pytest tests/test_chat_goals.py` — existing PCAP and credential goal tests must still pass.

---

## Component 3 — General Phase Directive (`core/llm_utils.py`)

### Goal
Prevent the `[CURRENT PHASE: RECONNAISSANCE]` fallback from forcing tool calls on conversational and general tasks.

### Root cause in code
`DynamicContextBuilder.build_context()` already handles `dev`, `hash`, `pcap`, and `credential/log-search` intents. For everything else, it falls through to the default branch:

```python
# llm_utils.py — current fallback (~bottom of build_context)
# No tools used yet
return (
    "\n[CURRENT PHASE: RECONNAISSANCE]\n"
    "No recon performed yet. "
    "Start with: system_info, dns_lookup, ping_sweep.\n"
    "Do NOT emit plain text — call a tool immediately.\n"  # ← fires on conversational tasks
)
```

Any question that is not classified as `dev`, `hash`, `pcap`, or a credential follow-up gets this directive, which forces tool calls even for a question like "what are my options here?"

### Change

Add a `general` intent branch before the recon fallback:

```python
# llm_utils.py — add before the has_recon/has_enum/has_report branches
from core.task_intent import detect_mission_kind

kind = detect_mission_kind(latest)

# ... existing hash/pcap/dev/credential branches ...

# General / conversational / analysis intent
if kind == "general" and not has_recon and not has_enum and not has_report:
    if not _SEARCH_INTENT_RE.search(latest_lower):
        return (
            "\n[CURRENT PHASE: GENERAL / ANALYSIS]\n"
            "This is a conversational, planning, or review request.\n"
            "Respond directly in plain text. Tool use is optional — "
            "only call a tool if it directly serves the user's question.\n"
            "Do NOT force network recon tools (port_scan, dns_lookup, ping_sweep) "
            "unless the user explicitly asks for them.\n"
        )
```

Note: `detect_mission_kind()` already exists in `core/task_intent.py` and is already imported via `context_router.py`. In `llm_utils.py` it needs a local import or the call can delegate to the existing `from core.task_intent import detect_mission_kind` at the top of the method.

### Tests
- **New:** `tests/test_false_positive_gating.py`, Test 2 — assert that `DynamicContextBuilder.build_context()` for messages like `"review this Python script"` emits `GENERAL / ANALYSIS` and not `RECONNAISSANCE`.
- **Regression:** `pytest tests/test_context_trim.py` — `test_anchor_phase_pcap_with_trailing_nudge` and `test_detect_mission_kind` must still pass.

---

## Component 4 — False-Positive Gating (`core/chat_goals.py`)

### Goal
Prevent the credential/PCAP goal from firing on conversational or coding prompts that happen to contain words like `login`, `password`, or `xml`.

### Root cause in code

Two compounding problems:

**Problem A — `_session_had_credential_work()` is too broad:**
```python
# chat_goals.py
def _session_had_credential_work(session: list[dict]) -> bool:
    for msg in reversed((session or [])[-50:]):
        if msg.get("role") != "tool":
            continue
        if msg.get("name") in (
            "analyze_pcapng", "grep_file", "find_and_grep",
            "crack_hash", "write_file", "read_file",  # ← read_file fires on any prior session
        ):
            return True
    return False
```

`read_file` is used in virtually every session (reading configs, plans, reports). This makes `_session_had_credential_work()` return `True` for almost any follow-up message that matches `_CREDENTIAL_SESSION_RE`.

**Problem B — `_CREDENTIAL_SESSION_RE` matches intent-neutral words:**
```python
_CREDENTIAL_SESSION_RE = re.compile(
    r"\b(expand.*search|search.*term|grep|filter|password|xml|xmlobj|login|salt|verbose|credential|"
    r"complete.*previous|previous task|analyze.*filter)\b",
    re.I,
)
```

Words like `login`, `xml`, `filter` are common in dev and analysis contexts. Combined with Problem A, any question like *"write a Python script to handle login"* after a session that called `read_file` fires the credential goal.

### Changes

**4a. Restrict `_session_had_credential_work()` to forensic-specific tools:**
```python
# Only tools that are unambiguously forensic/credential work
_CREDENTIAL_TOOLS = frozenset({
    "analyze_pcapng",
    "grep_file",
    "find_and_grep",
    "crack_hash",
})

def _session_had_credential_work(session: list[dict]) -> bool:
    for msg in reversed((session or [])[-50:]):
        if msg.get("role") != "tool":
            continue
        if msg.get("name") in _CREDENTIAL_TOOLS:
            # Also require that the tool succeeded (not just that it was called)
            try:
                import json as _json
                payload = _json.loads(msg.get("content", "{}"))
                if isinstance(payload, dict) and payload.get("success") is False:
                    continue
            except Exception:
                pass
            return True
    return False
```

**4b. Add an explicit coding/planning exclusion to `_build_credential_session_goal()`:**
```python
_DEV_INTENT_RE = re.compile(
    r"\b(write|script|python|\.py|code|implement|create|build|plan|options?|review)\b",
    re.I,
)

def _build_credential_session_goal(message: str, session: list[dict] | None) -> ChatGoals | None:
    if session is None:
        return None
    if not _CREDENTIAL_SESSION_RE.search(message or ""):
        return None
    # Do not fire if the prompt is primarily a dev/coding/planning request
    if _DEV_INTENT_RE.search(message or ""):
        return None
    if not _session_had_credential_work(session):
        return None
    # ... rest of the builder unchanged
```

**4c. (Gating Policy Rule — from `generalization_plan.md`)** Document this as an explicit policy comment at the top of the registry section:

```python
# GATING POLICY: Hard required_tools are ONLY attached to credential/pcap/forensic goals
# (or when the user explicitly names a tool). For conversational, coding, and analysis
# intents, match_message() must return None so tool use remains optional.
```

### Tests
- **New:** `tests/test_false_positive_gating.py`, Test 3 — assert that prompts like `"write a Python script to handle login validation"` after a session with `analyze_pcapng` do **not** match a credential goal.
- **New:** `tests/test_true_negative_gating.py` — assert that prompts like `"extract the password from last_capture.pcapng"` and `"crack this target hash"` still correctly match the PCAP and hash crack goals with hard gates.
- **Regression:** `pytest tests/test_chat_goals.py` — all existing 15 tests must still pass.

---

## Component 5 — Configurable Multiline Console UX (`console.py` + `config.yaml`)

### Goal
Make the `Ctrl+Enter` submit binding configurable and add graceful fallback for terminals that do not support it.

### Changes

**5a. Add key to `config.yaml`:**
```yaml
console:
  submit_binding: "ctrl-enter"   # options: "ctrl-enter", "esc-enter", "enter"
```

**5b. Read binding in `AgentConsole.__init__` and configure in `run_repl()`:**
```python
# console.py — in __init__:
console_cfg = self.agent.config.get("console", {})
self.submit_binding = console_cfg.get("submit_binding", "ctrl-enter")

# In run_repl(), replace the hardcoded prompt_session init:
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.filters import is_done

kb = KeyBindings()
multiline_mode = True

if self.submit_binding == "ctrl-enter":
    try:
        @kb.add("c-j")   # Ctrl+Enter sends c-j in most terminals
        def _submit(event):
            event.current_buffer.validate_and_handle()
    except Exception:
        console.print("[yellow]⚠ Ctrl+Enter binding not supported — using Esc+Enter.[/yellow]")
        multiline_mode = True   # prompt_toolkit default

prompt_session = PromptSession(key_bindings=kb if self.submit_binding == "ctrl-enter" else None)
```

---

## Testing Strategy

### New test files to create

| File | Covers |
|------|--------|
| `tests/test_exhausted_partial_status.py` | Component 1 — `PARTIAL` state, `may_complete_turn`, `write_file` unblocked |
| `tests/test_false_positive_gating.py` | Components 2, 3, 4 — coding/conversational prompts get no hard gate |
| `tests/test_true_negative_gating.py` | Component 4 — PCAP/credential prompts still get hard gate |

### Regression commands (run before and after each component)

```powershell
pytest tests/test_task_plan.py
pytest tests/test_chat_goals.py
pytest tests/test_tool_hints.py
pytest tests/test_context_trim.py
pytest tests/test_completion_guards.py
```

### Manual verification

After all components are applied, run `python console.py` and verify:

1. `chat` mode — ask *"plan a couple of options for handling login in Python"* → should respond immediately with prose, no `find_and_grep` forced, no repeated `[SYSTEM] Task complete` loops.
2. `chat` mode — ask *"extract the password from last_capture.pcapng"* → should still enforce `analyze_pcapng` before completing.
3. `chat` mode — run a `crack_hash` that returns `exhausted` → should write `pwd.txt` with `NOT CRACKED (exhausted)` and exit cleanly.

---

## What this plan does NOT change

- The PCAP, port scan, and hash crack goal builders remain unchanged. All existing forensic workflow behavior is preserved.
- `WriteGuard`, `ExecutionPolicy`, `AgentOutputParser`, and `ContextRouter` are untouched.
- No changes to tool schemas, knowledge playbooks, or the audit trail.
- All 10 existing regression tests listed in `MEMORY.md` must continue to pass.
