# Codebase Mapping & Full Code Check Plan

This plan organizes all files in the `pwsh_agent` codebase (excluding session directories) and defines a systematic auditing plan to verify architectural boundaries, code quality, safety guardrails, knowledge/tools context injection strategy, and compliance with the project stack (Python, PowerShell, Ollama qwen2.5-coder:7b-openclaw).

---

## Codebase File Map

### 1. Entrypoints & Runners
*   [agent.py](file:///c:/Users/soyko/Documents/pwsh_agent/agent.py) - Main orchestrator & runner.
*   [console.py](file:///c:/Users/soyko/Documents/pwsh_agent/console.py) - Console-based interactive user interface.
*   [mcp_server.py](file:///c:/Users/soyko/Documents/pwsh_agent/mcp_server.py) - Model Context Protocol (MCP) server wrapper.
*   [audit.py](file:///c:/Users/soyko/Documents/pwsh_agent/audit.py) - Audit trail logs generator/verifier.
*   [inicio.bat](file:///c:/Users/soyko/Documents/pwsh_agent/inicio.bat) & [pulse.bat](file:///c:/Users/soyko/Documents/pwsh_agent/pulse.bat) - Batch scripts for starting/orchestrating the agent.

### 2. Core Engine (`/core`)
*   [capabilities.py](file:///c:/Users/soyko/Documents/pwsh_agent/core/capabilities.py) - Capabilities definitions and routing.
*   [chat_goals.py](file:///c:/Users/soyko/Documents/pwsh_agent/core/chat_goals.py) - Session goals management.
*   [context.py](file:///c:/Users/soyko/Documents/pwsh_agent/core/context.py) - Execution context and token management.
*   [context_router.py](file:///c:/Users/soyko/Documents/pwsh_agent/core/context_router.py) - Model-based context routing.
*   [credential_extract.py](file:///c:/Users/soyko/Documents/pwsh_agent/core/credential_extract.py) - Credential parsing/extraction from tool output.
*   [debug_log.py](file:///c:/Users/soyko/Documents/pwsh_agent/core/debug_log.py) - Debug logging utilities.
*   [execution_policy.py](file:///c:/Users/soyko/Documents/pwsh_agent/core/execution_policy.py) - Script execution sandbox/safety policies.
*   [facts_store.py](file:///c:/Users/soyko/Documents/pwsh_agent/core/facts_store.py) - Ephemeral/persistent fact management.
*   [intent_salvage.py](file:///c:/Users/soyko/Documents/pwsh_agent/core/intent_salvage.py) - Intent recovery when parses fail.
*   [intent_spec.py](file:///c:/Users/soyko/Documents/pwsh_agent/core/intent_spec.py) - Specification of user intents.
*   [llm_utils.py](file:///c:/Users/soyko/Documents/pwsh_agent/core/llm_utils.py) - Ollama interaction wrappers, prompt building.
*   [memory.py](file:///c:/Users/soyko/Documents/pwsh_agent/core/memory.py) - Short and long term agent memory.
*   [mission_evaluator.py](file:///c:/Users/soyko/Documents/pwsh_agent/core/mission_evaluator.py) - Evaluator checking if mission requirements are fulfilled.
*   [mission_progress.py](file:///c:/Users/soyko/Documents/pwsh_agent/core/mission_progress.py) - Progress tracker.
*   [parser.py](file:///c:/Users/soyko/Documents/pwsh_agent/core/parser.py) - ReAct loop parser (thoughts, actions, parameters).
*   [path_catalog.py](file:///c:/Users/soyko/Documents/pwsh_agent/core/path_catalog.py) - Directory structure lookup.
*   [query_anchor.py](file:///c:/Users/soyko/Documents/pwsh_agent/core/query_anchor.py) - RAG anchor identification.
*   [rag.py](file:///c:/Users/soyko/Documents/pwsh_agent/core/rag.py) - Simple local retrieval augmented generation.
*   [runtime_paths.py](file:///c:/Users/soyko/Documents/pwsh_agent/core/runtime_paths.py) - Dynamically resolved system path configuration.
*   [session_paths.py](file:///c:/Users/soyko/Documents/pwsh_agent/core/session_paths.py) - Session path resolvers.
*   [spill.py](file:///c:/Users/soyko/Documents/pwsh_agent/core/spill.py) - Spilling large inputs to file.
*   [task_intent.py](file:///c:/Users/soyko/Documents/pwsh_agent/core/task_intent.py) - Intended user task model.
*   [task_plan.py](file:///c:/Users/soyko/Documents/pwsh_agent/core/task_plan.py) - Decomposed plan tracking.
*   [tool_hints.py](file:///c:/Users/soyko/Documents/pwsh_agent/core/tool_hints.py) - Hint injectors for LLM usage.
*   [tool_index.py](file:///c:/Users/soyko/Documents/pwsh_agent/core/tool_index.py) - Dynamic lookup index for tools.
*   [working_state.py](file:///c:/Users/soyko/Documents/pwsh_agent/core/working_state.py) - Live agent workspace state container.
*   [write_guard.py](file:///c:/Users/soyko/Documents/pwsh_agent/core/write_guard.py) - Safety guardrails preventing destructive writes.
*   [__init__.py](file:///c:/Users/soyko/Documents/pwsh_agent/core/__init__.py) - Core package exports.

### 3. Tool Implementations & Definitions
*   [tools/intel.py](file:///c:/Users/soyko/Documents/pwsh_agent/tools/intel.py) - Intelligence/leak check/credential tools.
*   [tools/recon.py](file:///c:/Users/soyko/Documents/pwsh_agent/tools/recon.py) - Port scanners, DNS, pcap analysis tools.
*   [tools/__init__.py](file:///c:/Users/soyko/Documents/pwsh_agent/tools/__init__.py) - Tool exports and registry.
*   [tools_legacy.py](file:///c:/Users/soyko/Documents/pwsh_agent/tools_legacy.py) - Monolithic legacy tools module.

### 3b. Knowledge / Tool Playbooks & Schemas (`/knowledge`)

Top-level domain references (RAG-indexed by phase/tool tags):
*   [exploit_auditing.md](file:///c:/Users/soyko/Documents/pwsh_agent/knowledge/exploit_auditing.md) - LPE, weak perms, autorun, credential auditing reference.
*   [network_analysis.md](file:///c:/Users/soyko/Documents/pwsh_agent/knowledge/network_analysis.md) - Network/PCAP analysis domain knowledge.
*   [powershell_recon.md](file:///c:/Users/soyko/Documents/pwsh_agent/knowledge/powershell_recon.md) - PowerShell reconnaissance playbook.
*   [reverse_engineering.md](file:///c:/Users/soyko/Documents/pwsh_agent/knowledge/reverse_engineering.md) - Reverse engineering reference.

Per-tool playbooks (`knowledge/tools/`) — 27 files, each with YAML frontmatter `tools:` and `phase:` tags:
*   [tool_routing_static.md](file:///c:/Users/soyko/Documents/pwsh_agent/knowledge/tools/tool_routing_static.md) - Deterministic "when to use" routing table (injected every turn by `tool_index.py`).
*   [analyze_pcapng.md](file:///c:/Users/soyko/Documents/pwsh_agent/knowledge/tools/analyze_pcapng.md), [capture_packets.md](file:///c:/Users/soyko/Documents/pwsh_agent/knowledge/tools/capture_packets.md), [find_tshark.md](file:///c:/Users/soyko/Documents/pwsh_agent/knowledge/tools/find_tshark.md), [list_network_interfaces.md](file:///c:/Users/soyko/Documents/pwsh_agent/knowledge/tools/list_network_interfaces.md) - Network/PCAP tools.
*   [crack_hash.md](file:///c:/Users/soyko/Documents/pwsh_agent/knowledge/tools/crack_hash.md), [hash_identify.md](file:///c:/Users/soyko/Documents/pwsh_agent/knowledge/tools/hash_identify.md), [encode_decode.md](file:///c:/Users/soyko/Documents/pwsh_agent/knowledge/tools/encode_decode.md) - Exploit/crypto tools.
*   [dns_lookup.md](file:///c:/Users/soyko/Documents/pwsh_agent/knowledge/tools/dns_lookup.md), [ping_sweep.md](file:///c:/Users/soyko/Documents/pwsh_agent/knowledge/tools/ping_sweep.md), [port_scan.md](file:///c:/Users/soyko/Documents/pwsh_agent/knowledge/tools/port_scan.md), [system_info.md](file:///c:/Users/soyko/Documents/pwsh_agent/knowledge/tools/system_info.md) - Recon tools.
*   [http_headers_check.md](file:///c:/Users/soyko/Documents/pwsh_agent/knowledge/tools/http_headers_check.md), [ssl_analysis.md](file:///c:/Users/soyko/Documents/pwsh_agent/knowledge/tools/ssl_analysis.md), [cve_lookup.md](file:///c:/Users/soyko/Documents/pwsh_agent/knowledge/tools/cve_lookup.md) - Web/intel tools.
*   [host_exec.md](file:///c:/Users/soyko/Documents/pwsh_agent/knowledge/tools/host_exec.md), [run_script.md](file:///c:/Users/soyko/Documents/pwsh_agent/knowledge/tools/run_script.md), [read_file.md](file:///c:/Users/soyko/Documents/pwsh_agent/knowledge/tools/read_file.md), [write_file.md](file:///c:/Users/soyko/Documents/pwsh_agent/knowledge/tools/write_file.md), [grep_file.md](file:///c:/Users/soyko/Documents/pwsh_agent/knowledge/tools/grep_file.md), [find_file.md](file:///c:/Users/soyko/Documents/pwsh_agent/knowledge/tools/find_file.md), [append_note.md](file:///c:/Users/soyko/Documents/pwsh_agent/knowledge/tools/append_note.md) - Core file/exec tools.
*   [finding_create.md](file:///c:/Users/soyko/Documents/pwsh_agent/knowledge/tools/finding_create.md), [finding_list.md](file:///c:/Users/soyko/Documents/pwsh_agent/knowledge/tools/finding_list.md), [report_generate.md](file:///c:/Users/soyko/Documents/pwsh_agent/knowledge/tools/report_generate.md) - Reporting tools.
*   [sequentialthinking.md](file:///c:/Users/soyko/Documents/pwsh_agent/knowledge/tools/sequentialthinking.md), [expanded_playbooks.md](file:///c:/Users/soyko/Documents/pwsh_agent/knowledge/tools/expanded_playbooks.md) - Meta/planning tools.

### 4. Configuration, Scripts, & Metadata
*   [config.yaml](file:///c:/Users/soyko/Documents/pwsh_agent/config.yaml) - Ollama endpoint, model, state, and runtime settings.
*   [module-map.json](file:///c:/Users/soyko/Documents/pwsh_agent/module-map.json) - Architectural module mappings.
*   [tokenizer_config.json](file:///c:/Users/soyko/Documents/pwsh_agent/tokenizer_config.json) - Text processing properties.
*   [PROJECT_CONTEXT.md](file:///c:/Users/soyko/Documents/pwsh_agent/PROJECT_CONTEXT.md) - High-level repo specifications.
*   [scripts/reset_state.ps1](file:///c:/Users/soyko/Documents/pwsh_agent/scripts/reset_state.ps1) & [scripts/reset_state.py](file:///c:/Users/soyko/Documents/pwsh_agent/scripts/reset_state.py) - Utilities to clear temporary agent runs.

### 5. Agent Instructions & Memory
*   [state/AGENTS.md](file:///c:/Users/soyko/Documents/pwsh_agent/state/AGENTS.md) - Agent profiles.
*   [state/IDENTITY.md](file:///c:/Users/soyko/Documents/pwsh_agent/state/IDENTITY.md) - Agent system identity.
*   [state/SOUL.md](file:///c:/Users/soyko/Documents/pwsh_agent/state/SOUL.md) - Core behavior, reasoning style, and prompt guidance.
*   [state/USER.md](file:///c:/Users/soyko/Documents/pwsh_agent/state/USER.md) - User behavior profile.
*   [state/MEMORY.md](file:///c:/Users/soyko/Documents/pwsh_agent/state/MEMORY.md) - Agent long-term memory store.

### 6. Tests (`/tests`)
*   Contains 32 test scripts (`test_*.py`) validating parsing, execution policies, guards, memory, context, and capabilities routing.

---

## Full Code Check (Audit) Plan

We will perform the code check across 4 phases to verify structure, code quality, safety, and functionality.

### Phase 1: Architectural Boundaries Verification
1.  **Strict Isolation Rule**: Ensure no files under `/core` import from `/interfaces` (i.e. `agent.py`, `console.py`, `mcp_server.py`).
2.  **Tool Registry Integrity**: Verify all functions in `/tools` are properly declared, mapped to metadata, and exposed in `/tools/__init__.py`'s `__all__`.
3.  **State Contained Rule**: Confirm no module writes outputs or logs directly to the root; they must use directories resolved via `session_paths.py` or `/state`.

### Phase 2: Security & Safety Review
1.  **Write Guard Analysis**: Review [write_guard.py](file:///c:/Users/soyko/Documents/pwsh_agent/core/write_guard.py) rules to ensure system and critical system file overwrite/deletion requests are blocked.
2.  **Execution Policy Sanitization**: Verify [execution_policy.py](file:///c:/Users/soyko/Documents/pwsh_agent/core/execution_policy.py) correctly blocks unsafe PowerShell calls, shell escapes, or un-sanitized string interpolations.
3.  **Credential Leaks**: Review [credential_extract.py](file:///c:/Users/soyko/Documents/pwsh_agent/core/credential_extract.py) to confirm credentials found during checks are handled safely.

### Phase 3: Performance, LLM Prompting & Ollama Configuration
1.  **Token Trim Limits**: Verify [context.py](file:///c:/Users/soyko/Documents/pwsh_agent/core/context.py) handles contexts exceeding 8192 tokens properly.
2.  **Parser Robustness**: Verify [parser.py](file:///c:/Users/soyko/Documents/pwsh_agent/core/parser.py) handles edge cases in thoughts/action blocks without hanging, and check the performance of [intent_salvage.py](file:///c:/Users/soyko/Documents/pwsh_agent/core/intent_salvage.py).
3.  **Model Configuration**: Verify Ollama configuration values in [config.yaml](file:///c:/Users/soyko/Documents/pwsh_agent/config.yaml) match optimal values.

### Phase 4: Knowledge/Tools Context Injection Redesign Implementation

Based on the audit and design feedback, we will implement the following changes to prevent predisposing the agent and simplify tool context injection:

1.  **Remove Always-On Static Routing**:
    - Modify [context_router.py](file:///c:/Users/soyko/Documents/pwsh_agent/core/context_router.py) to remove the unconditional injection of `tool_routing_static.md`.
2.  **Remove Phase-Based Tool Group Bias**:
    - In `ContextRouter._derive_tool_set()`, remove the block that automatically adds broad tool sets (like `_DEV_TOOLS`, `_RECON_TOOLS`, `_NETWORK_TOOLS`) based on `phase_label`.
    - Tools will instead be derived strictly from query capabilities, direct keyword fallbacks, or recent successful tools.
3.  **Inject Matched Tool Schemas**:
    - Add a helper `_get_tool_schemas(cls, tool_names: list[str])` to `ContextRouter` that pulls the JSON schema definitions for matched tools from `tools.TOOLS_SCHEMA`.
    - Inject these schemas under a `### RELATED TOOL SCHEMAS ###` header.
4.  **Consolidate and Clean Up Tests**:
    - Update [test_rag_tools.py](file:///c:/Users/soyko/Documents/pwsh_agent/tests/test_rag_tools.py) to verify matched tool schemas are correctly injected and remove tests expecting always-on static routing.

## Verification Plan

### Automated Tests
- Run `pytest tests/` to confirm that all tests pass, including the updated `test_rag_tools.py`.
- Run specific tests:
  ```powershell
  .venv\Scripts\python.exe -m pytest tests/test_rag_tools.py
  .venv\Scripts\python.exe -m pytest tests/test_false_positive_gating.py
  ```

### Manual Verification
- Verify tool execution and context formatting in console mode.

