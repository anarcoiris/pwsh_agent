# 🧠 MEMORY.md - Long-Term Memory

## Project Overview: Pulse PowerShell ReAct Agent
An advanced autonomous local Windows developer, system auditor, and automation companion running natively in PowerShell + Python + Ollama.

## 🏗️ Core Architecture (Updated 2026-05-30)
- **Local Runtime**: Native Windows execution using the `py -3.10` launcher and virtual environments.
- **Cognitive Loop**: Clean ReAct engine mapping reasoning steps and sequential thoughts to local tool execution.
- **No SCM / Docker**: Pure native implementation. Direct, safe local command execution under close operator review.

## 📈 Recent Milestones
1. **Python 3.10 Venv Alignment**: Reconfigured `inicio.bat` launcher to strictly target the `py -3.10` launcher, preventing cross-version conflicts.
2. **Identity Implementation**: Created the core operational soul and identity blueprint documents (`SOUL.md`, `IDENTITY.md`, `USER.md`, `AGENTS.md`).
3. **Tool Parser Fix (2026-05-30)**: Fixed critical bug where `core/parser.py` only extracted `<tool_call>` XML tags. Ollama/qwen2.5-coder emits tool calls as bare JSON or fenced blocks — now parsed via 5 fallback paths. Regression: `tests/test_parser_fix.py`.
4. **Deliverable Pipeline (2026-05-30)**: `TaskIntentExtractor`, `WriteGuard`, and `append_note` prevent substituting `workspace/plan.md` for user-requested code files. `chat_turn` verifies deliverables exist on disk before closing. Regression: `tests/test_task_intent.py`, `tests/test_append_note.py`, `tests/test_write_guard.py`.
5. **PowerShell write_file sanitizer (2026-05-30)**: `_sanitize_powershell_content()` auto-fixes LLM `.ps1` line-continuation backticks. Regression: `tests/test_ps1_sanitize.py`.

## ✅ Task Closure — Agent Tool Pipeline Stabilization (2026-05-30)

**Status: IMPLEMENTED and regression-tested.** Safe for follow-on jobs to build on this baseline.

### Problems solved (session evidence)

| Issue | Resolution | Verified by |
|-------|------------|-------------|
| Tools never executed (JSON in text, parser empty) | Parser paths 3–5 + `salvage_tool_call()` | `test_parser_fix.py`, chat `write_file`/`read_file` OK |
| `helloworld.ps1` broken PowerShell | `_sanitize_powershell_content()` on `.ps1` write | Manual `host_exec`, `test_ps1_sanitize.py` |
| Agent wrote `plan.md` instead of `watcher.py` | `WriteGuard` + `TaskIntentExtractor` + chat nudge | `test_write_guard.py`, `watcher/watcher.py` on disk |
| `plan.md` overwritten each turn | `append_note` (timestamped append-only) | `test_append_note.py`, multi-line `workspace/plan.md` |
| Chat drift to network recon | `DynamicContextBuilder` DEVELOPMENT phase + CHAT MODE directive | Operator session logs |
| False "Mission complete" / JSON in panel | `_finalize_chat_response` disk check + tool summary | Warning when deliverable missing |

### Current success criteria (operator smoke)

After restarting `console.py`, a dev/chat prompt like:

`Write watcher.py in the watcher folder. Do not run network tasks.`

Should produce:

- Audit: `write_file` with path `watcher/watcher.py` (not only `workspace/plan.md`)
- Disk: `watcher/watcher.py` exists with Python source
- Progress: `workspace/plan.md` gains **appended** `[timestamp | session:…]` lines (not single-line overwrite)
- Response: tool summary or prose — not raw `{"name":…}` JSON; `⚠️` prefix if deliverable still missing

### Regression command (run before changing parser/agent/tools)

```powershell
python tests/test_parser_fix.py
python tests/test_ps1_sanitize.py
python tests/test_task_intent.py
python tests/test_append_note.py
python tests/test_write_guard.py
```

All five must print success lines.

### Known limitations (not bugs — model/runtime)

- Ollama `qwen2.5-coder` still returns `native_tool_calls: 0`; text fallback parser remains mandatory.
- Model may need 2+ turns or system nudge before writing deliverable; `WriteGuard` blocks false completion via plan.md only.
- `clear` in console resets **session JSON** only; `workspace/plan.md` on disk persists until manually cleared.
- `watcher/watcher.py` in repo uses `watchdog` — requires `pip install watchdog` to run (separate from agent pipeline).

### Key files for next jobs

| Module | Role |
|--------|------|
| `core/parser.py` | Tool-call extraction (5 paths) + code blocks → `write_file` |
| `core/task_intent.py` | Parse deliverables from user message |
| `core/write_guard.py` | Redirect/block misrouted `write_file` |
| `tools_legacy.py` | `write_file`, `append_note`, PS1 sanitizer |
| `agent.py` | `chat_turn`, `WriteGuard` hook, `_finalize_chat_response` |
| `core/llm_utils.py` | `DynamicContextBuilder` dev vs recon phases |

## 🛡️ Tool Execution Guardrails (DO NOT REGRESS)
- **Parser must implement all 5 documented fallback paths** in `AgentOutputParser._discover_and_extract_tool_calls`: native Ollama, `<tool_call>` XML, fenced ` ```json ` blocks, bare inline JSON, fenced ` ```python` / ` ```powershell` → `write_file`.
- **Ollama model behavior**: `qwen2.5-coder:7b` returns `native_tool_calls: 0` consistently — text fallback parser is mandatory, not optional.
- **`sequentialthinking` is local Python** (`core/llm_utils.SequentialThinkingEngine`); MCP server (`mcp_server.py`) is a separate stdio interface — console agent does NOT call MCP.
- **`parser_reflection` must salvage real tool calls** before injecting meta-`sequentialthinking` thoughts — otherwise the agent hallucinates successful execution.
- **Run `python tests/test_parser_fix.py`** after any change to `core/parser.py`, `agent.py` OllamaAdapter, or `RetryOrchestrator`.
- **Run `python tests/test_ps1_sanitize.py`** after changes to `write_file` or `_sanitize_powershell_content`.
- **PowerShell write_file sanitizer (2026-05-30)**: LLM embeds multiline `.ps1` in JSON with spurious trailing `` ` `` before `\n` (line-continuation) and single-quoted `-Object` strings where `` `n `` is literal. `_sanitize_powershell_content()` in `tools_legacy.py` auto-fixes on write. Audit evidence: `audit_trail/2026-05-29.jsonl` write_file for helloworld.ps1 lines 5/6/10 had `ends_backtick=True`.
- **CodeBlockExtractor + chat anti-drift (2026-05-30)**: ` ```python ` / ` ```powershell ` blocks now map to `write_file` (path inferred from user message). `DynamicContextBuilder` suppresses recon hints for dev/file tasks. `chat_turn` injects CHAT MODE directive, salvages code blocks before stalling, warns if model claims "saved" without deliverable on disk.
- **Deliverable guard + append_note (2026-05-30)**: See Task Closure section above. Never regress: deliverable path from user message; `append_note` for `workspace/plan.md` notes only.
- **Smoke test (file tools)**: `test your read, write, powershell and execution tools with a new test_tools.txt file` — expect `write_file`, `read_file`, `host_exec` in audit.
- **Smoke test (deliverable)**: watcher.py prompt above — expect `watcher/watcher.py` on disk.

## 📊 System Operations & Stats
- **Last Run**: 2026-05-30 (Session: `default`, Persona: `LEAD`)
- **Total Auditing Days**: 2