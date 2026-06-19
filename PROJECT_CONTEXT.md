# Repo context

Stack: Python 3.10+, PowerShell, Ollama (qwen2.5-coder:7b-instruct)  
Specialists: LEAD + workspace / web / recon / forensic / crypto (`core/specialists.py`, `config.agent.prompt_pack_mode`)

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
- Ollama context: `config.yaml` sets `num_ctx: 16384` (execute) and `num_ctx_planner: 32768` (plan/evaluate). Agent canon: [docs/AGENT_CANON.md](docs/AGENT_CANON.md).
- Tool executions are audited by `audit.py` to `state/audit_trail/`.
- Memory logs are appended to `state/memory/` (and `memory/` daily notes).

Plans index (`docs/plans/`):

- [session_closure_20260604.md](docs/plans/session_closure_20260604.md) — fixes verified this session
- [specialist_handoff_plan.md](docs/plans/specialist_handoff_plan.md) — prompt pack + handoff (done)
- [web_auth_html_pipeline_plan.md](docs/plans/web_auth_html_pipeline_plan.md) — router/HTML login (next)
- [implementation_plan.md](docs/plans/implementation_plan.md) — batch notes + artifact compaction (done)
- [multi_purpose_agent_design.md](docs/plans/Generalization/multi_purpose_agent_design.md) — multi-purpose generalization plan (partially done)
- [context_trim_plan.md](docs/plans/context_trim_plan.md) — context audit checklist
- [README.md](docs/plans/README.md) — plans index
