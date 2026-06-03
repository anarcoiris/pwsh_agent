# Repo context

Stack: Python 3.10+, PowerShell, Ollama (qwen2.5-coder:7b-openclaw)
Module layout:
- /core: The ReAct engine, LLM utils, parsers, and execution policies.
- /tools: The extensible plugin system (network, system, intelligence) and `tools_legacy.py` monolith.
- /state: Identity files (`AGENTS.md`, `MEMORY.md`), session history, audit trails, and findings databases.
- /interfaces: Entrypoints like `console.py`, `agent.py`, and `mcp_server.py`.

Boundary policy:
- `core/` modules must not import from `/interfaces`.
- `tools/` must export all functions via `__init__.py` using `__all__` so that `agent.py` can dynamically load the registry.
- All stateful outputs, logs, and configurations must be written to `/state` to avoid cluttering the repository root.

Execution:
- Standard executions must funnel through `agent.py`.
- Ollama context: `config.yaml` sets `num_ctx: 8192` and `num_predict: 3072` (4096 for synthesis). Agent trim budgets (`max_context_tokens`, `reserve_generation_tokens`, `reserve_injection_tokens`) are aligned to the same 8192 window so persisted history + injections fit what Ollama receives. Reload the model with context length ≥8192 if the server was started with a smaller window.
- Tool executions are audited by `audit.py` to `state/audit_trail/`.
- Memory logs are appended to `state/memory/`.
