"""
agent.py — Cognitive ReAct Core for Pulse Windows Agent.

Upgraded with all father-repo reasoning/stepping features:
- AgentOutputParser     — 4-path fallback + recursive tool-call extraction
- CodeBlockExtractor    — PS/cmd blocks auto-dispatched to host_exec
- ArgumentNormalizer    — strip URLs, shell prompts, fix port collisions
- ResultCompactor       — tool-aware truncation (port_scan, ping, http, pcap)
- RetryOrchestrator     — parser_reflection() self-correction thought
- DynamicContextBuilder — RECON/SCAN/ENUM/REPORT phase hints per turn
- AgentContextManager   — JSON session persistence + ContextCompactor
- SequentialThinkingEngine — full branches/revisions/needsMoreThoughts
- Duplicate call deduplication via tools_called set
- MIN_TOOLS_BEFORE_COMPLETE guard
- Cancellation via asyncio.Event
- Structured final synthesis (### Summary / Findings / Next Steps)
"""

import asyncio
import hashlib
import json
import logging
import re
import time
import yaml
from pathlib import Path
from typing import Any

import httpx
from ollama import AsyncClient

import tools
from audit import AuditEntry, get_audit
from core.context import AgentContextManager
from core.context_router import ContextRouter
from core.query_anchor import resolve_anchor_query
from core.llm_utils import (
    ArgumentNormalizer,
    DynamicContextBuilder,
    ResultCompactor,
    RetryOrchestrator,
    SequentialThinkingEngine,
)
from core.parser import AgentOutputParser
from core.task_intent import TaskIntent, TaskIntentExtractor, _is_credential_deliverable, path_matches_deliverable
from core.task_plan import (
    TaskPlanTracker,
    load_plan_state,
    save_plan_state,
    clear_plan_state,
    _looks_like_placeholder_file,
    _looks_like_extraction_draft,
)
from core.session_paths import (
    ensure_session_layout,
    generate_session_id,
    load_active_session_id,
    plan_note_rel,
    save_active_session,
    scratchpad_file,
    status_note_rel,
    session_log_rel,
)
from core.spill import maybe_spill_text
from core.facts_store import summarize_facts, update_from_tool
from core.working_state import (
    WorkingMemory,
    build_current_state,
    load_working_memory,
    save_working_memory,
)
from core.chat_goals import ChatGoals, ChatGoalGuard, ChatGoalRegistry
from core.write_guard import WriteGuard
from core.execution_policy import ExecutionPolicy, set_pip_near
from core.runtime_paths import app_root, workspace_root
from core.mission_progress import MissionProgressTracker
from core.mission_evaluator import MissionEvaluator

logger = logging.getLogger("pwsh_agent.agent")


# ──────────────────────────────────────────────────────────────────────────────
# Ollama Adapter (inline, adapted from father's llm_adapter.py)
# ──────────────────────────────────────────────────────────────────────────────

class OllamaAdapter:
    """
    Async Ollama wrapper with:
    - Retry loop (3 attempts, exponential back-off)
    - Dynamic phase-context injection
    - Parser fallback for missed tool calls
    """

    def __init__(
        self,
        host: str,
        model: str,
        parser: AgentOutputParser,
        *,
        num_ctx: int = 24576,
        num_predict: int = 3072,
        injection_budget_chars: int = 8000,
    ):
        self.host = host
        self.model = model
        self.parser = parser
        self.num_ctx = num_ctx
        self.num_predict = num_predict
        self.injection_budget_chars = injection_budget_chars
        self.client = AsyncClient(host=host, timeout=httpx.Timeout(300.0))

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools_schema: list[dict[str, Any]] | None = None,
        options: dict | None = None,
        max_retries: int = 3,
        task_intent: TaskIntent | None = None,
        anchor_query: str | None = None,
        session_snippet: str | None = None,
        plan_block: str | None = None,
        current_state: str | None = None,
    ) -> dict[str, Any]:
        try:
            for injection in ContextRouter.build_injections(
                messages,
                task_intent,
                anchor_query=anchor_query,
                session_snippet=session_snippet,
                plan_block=plan_block,
                current_state=current_state,
                injection_budget_chars=self.injection_budget_chars,
            ):
                messages = messages + [injection]
        except Exception as e:
            logger.warning("ContextRouter injection error: %s", e)

        _options = {
            "temperature": 0.3,
            "num_ctx": self.num_ctx,
            "num_predict": self.num_predict,
        }
        if options:
            _options.update(options)

        anchor = (anchor_query or "").strip() or resolve_anchor_query(messages)
        self.parser.set_user_context(anchor)
        latest_user = anchor

        for attempt in range(1, max_retries + 1):
            try:
                response = await self.client.chat(
                    model=self.model,
                    messages=messages,
                    tools=tools_schema,
                    options=_options,
                    stream=False,
                )

                msg: dict[str, Any] = {
                    "role":    response.message.role,
                    "content": response.message.content or "",
                }

                # Extract native tool_calls from SDK response
                if response.message.tool_calls:
                    msg["tool_calls"] = [
                        {
                            "function": {
                                "name":      tc.function.name,
                                "arguments": tc.function.arguments,
                            }
                        }
                        for tc in response.message.tool_calls
                    ]

                # Parser fallback: scan content for missed tool calls
                if not msg.get("tool_calls"):
                    extracted = self.parser.discover_tool_calls(
                        msg["content"], user_context=latest_user
                    )
                    if extracted:
                        msg["tool_calls"] = extracted

                return {"message": msg}

            except (httpx.RequestError, ValueError) as e:
                logger.warning("Ollama connection error (attempt %d): %s", attempt, e)
                await asyncio.sleep(2 ** attempt)

        return {"message": {"role": "assistant", "content": "ERROR: Ollama unreachable."}}


# ──────────────────────────────────────────────────────────────────────────────
# ReAct Agent
# ──────────────────────────────────────────────────────────────────────────────

class ReActAgent:
    """
    Autonomous cognitive agent using the ReAct pattern.

    Wires together:
    - AgentOutputParser   for extraction
    - OllamaAdapter       for LLM calls with retry + phase hints
    - AgentContextManager for persistent session history
    - SequentialThinkingEngine for reasoning chains
    - RetryOrchestrator   for parser-reflection self-correction
    - ArgumentNormalizer  before tool dispatch
    - ResultCompactor     after tool execution
    """

    MIN_TOOLS_BEFORE_COMPLETE: int = 4
    MIN_SUBSTANTIVE_BEFORE_COMPLETE: int = 2

    def __init__(self, session_id: str | None = None):
        self.workspace_root = workspace_root()
        self.app_root       = app_root()
        self.config         = self._load_config()

        ollama_cfg = self.config.get("ollama", {})
        self.base_url      = ollama_cfg.get("base_url", "http://localhost:11435")
        self.default_model = ollama_cfg.get("default_model", "qwen2.5-coder:7b")
        self.conversational_model = ollama_cfg.get("conversational_model")
        self.evaluator_temperature = float(ollama_cfg.get("evaluator_temperature", 0.1))
        self.num_ctx = int(ollama_cfg.get("num_ctx", 24576))
        self.num_predict = int(ollama_cfg.get("num_predict", 3072))
        self.num_predict_synthesis = int(ollama_cfg.get("num_predict_synthesis", 4096))

        agent_cfg = self.config.get("agent", {})
        self.max_steps = agent_cfg.get("max_steps", 15)
        self.max_thoughts = agent_cfg.get("max_thoughts", 15)
        self.max_context_chars = int(agent_cfg.get("max_context_chars", 47_000))
        self.max_total_messages = int(agent_cfg.get("max_total_messages", 80))
        self.max_tool_result_chars = int(agent_cfg.get("max_tool_result_chars", 22_000))
        self.injection_budget_chars = int(agent_cfg.get("injection_budget_chars", 8000))
        # Phase 2: how many recent turns of raw history to send to the LLM. The
        # full log is still persisted to disk; CURRENT STATE carries continuity.
        self.history_window_turns = int(agent_cfg.get("history_window_turns", 8))
        self.max_context_tokens = int(agent_cfg.get("max_context_tokens", 0))
        self.reserve_generation_tokens = int(
            agent_cfg.get("reserve_generation_tokens", self.num_predict)
        )
        self.reserve_injection_tokens = int(
            agent_cfg.get("reserve_injection_tokens", max(1024, self.injection_budget_chars // 4))
        )

        ResultCompactor.configure_max_chars(self.max_tool_result_chars)

        # ── Specialist & safety state ──────────────────────────────────────
        self.active_specialist: str = "lead"
        self.network_mode: str      = "SANDBOX"

        # ── Core engines ───────────────────────────────────────────────────
        self.thinking_engine    = SequentialThinkingEngine(max_thoughts=self.max_thoughts)
        self.retry_orchestrator = RetryOrchestrator()

        # Tools registry
        self.tools_registry: dict[str, Any] = {
            "sequentialthinking": self.thinking_engine.process_thought,
        }
        for name in tools.__all__:
            if name not in ("SequentialThinkingEngine", "TOOLS_SCHEMA", "sequentialthinking"):
                self.tools_registry[name] = getattr(tools, name)

        self.parser  = AgentOutputParser(self.tools_registry)
        self.adapter = OllamaAdapter(
            host=self.base_url,
            model=self.default_model,
            parser=self.parser,
            num_ctx=self.num_ctx,
            num_predict=self.num_predict,
            injection_budget_chars=self.injection_budget_chars,
        )
        self.mission_evaluator: MissionEvaluator | None = None
        if self.conversational_model:
            self.mission_evaluator = MissionEvaluator(
                host=self.base_url,
                model=self.conversational_model,
                temperature=self.evaluator_temperature,
            )

        # Session persistence
        self.session_id  = session_id or load_active_session_id()
        self.session_note_paths = ensure_session_layout(self.session_id)
        save_active_session(self.session_id)
        self.ctx_manager = AgentContextManager(
            mode="autonomous",
            session_id=self.session_id,
            max_total_context=self.max_total_messages,
            max_context_chars=self.max_context_chars,
            max_tool_result_chars=self.max_tool_result_chars,
            max_context_tokens=self.max_context_tokens,
            reserve_generation_tokens=self.reserve_generation_tokens,
            reserve_injection_tokens=self.reserve_injection_tokens,
        )

        # Cancellation
        self._cancel_event = asyncio.Event()
        self._anchor_query: str = ""
        self._active_intent: TaskIntent | None = None
        self._mission_goals: ChatGoals | None = None
        self._mission_tools_executed: list[str] = []
        self._mission_tracker: MissionProgressTracker | None = None
        self._pending_script_failure: dict[str, Any] | None = None
        self._last_script_path: str | None = None
        self._chat_tool_events: list[dict[str, Any]] = []
        # Transient ephemeral draft (e.g. login_forms) injected once via CURRENT STATE.
        self._pcap_draft: str | None = None
        self._pcap_draft_raw: str | None = None
        self._credential_pairs: list[dict[str, str]] = []
        self._crack_results: list[dict[str, Any]] = []
        self._last_pcap_path: str | None = None
        # Volatile reasoning scratch (Phase 2). Persisted small for continuity.
        self._working_memory: WorkingMemory = WorkingMemory()
        # Last tool result head, surfaced once via CURRENT STATE (not re-narrated).
        self._last_tool_head: str = ""

        # Initialise system prompt if fresh session
        if not self.ctx_manager.has_system():
            self._init_system_prompt()

    # ── Configuration ──────────────────────────────────────────────────────

    def _load_config(self) -> dict:
        config_path = self.app_root / "config.yaml"
        if config_path.exists():
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    return yaml.safe_load(f) or {}
            except Exception as e:
                logger.warning("Error loading config.yaml: %s", e)
        return {}

    # ── System Prompt ──────────────────────────────────────────────────────

    def _init_system_prompt(self):
        """Build the full system prompt and seed the context manager."""
        overlays = {
            "lead": (
                "ROLE OVERLAY: LEAD / ORCHESTRATOR\n"
                "- Strategic planning, phase mapping, final report consolidation.\n"
                "- Avoid getting trapped in minor errors. Retain broad tactical awareness.\n"
            ),
            "network": (
                "ROLE OVERLAY: NETWORK SPECIALIST\n"
                "- Interface configuration, PCAP capture, plaintext credential discovery.\n"
                "- Use dns_lookup, ping_sweep, port_scan, capture_packets, analyze_pcapng.\n"
            ),
            "re": (
                "ROLE OVERLAY: RE EXPERT\n"
                "- Static/dynamic binary analysis, strings, assembly, functional logic.\n"
                "- Work patiently step-by-step; use host_exec for local binaries.\n"
            ),
            "exploit": (
                "ROLE OVERLAY: AUDITOR / EXPLOIT DEV\n"
                "- Configuration audits, cryptographic cracking, credential verification.\n"
                "- Use crack_hash, hash_identify, encode_decode, cve_lookup.\n"
            ),
        }
        overlay = overlays.get(self.active_specialist, overlays["lead"])

        plan_path = plan_note_rel(self.session_id)
        status_path = status_note_rel(self.session_id)
        session_log_path = session_log_rel(self.session_id)

        identity_context = ""
        for fn in ("SOUL.md", "IDENTITY.md", "USER.md"):
            fp = self.app_root / "state" / fn
            if fp.exists():
                try:
                    identity_context += f"\n--- {fn} ---\n{fp.read_text(encoding='utf-8')}\n"
                except Exception:
                    pass

        prompt = (
            "You are Pulse Windows Agent — a highly skilled autonomous AI security auditor "
            "executing natively on the user's Windows OS via PowerShell.\n\n"

            f"SPECIALIST MODE: {self.active_specialist.upper()}\n"
            f"{overlay}\n"
            f"SECURITY BADGE: [{self.network_mode}]\n"
            "No SCM/Docker isolation. Maintain absolute safety and transparent intent.\n\n"

            "NATIVE PROFILE:\n"
            f"{identity_context}\n\n"

            "COGNITIVE WORKFLOW (ReAct):\n"
            "1. Plan briefly (one sequentialthinking call max), then act. Run tools one at a time and inspect each result before the next.\n"
            f"2. Progress notes: append_note on `{plan_path}`/`{status_path}` (never write_file for status). Deliverables: write_file to the exact user path. Per-task notes: append_note under workspace/sessions/{self.session_id}/scratchpads/.\n"
            "3. Register significant discoveries with finding_create. In mission mode, declare MISSION_COMPLETE only after producing evidence.\n\n"

            "TOOL CALL FORMAT:\n"
            "Use the exact `<tool_call>` XML tags with ONLY valid JSON inside. No `<tool_response>`, no XML child nodes, no raw terminal syntax.\n"
            "<tool_call>\n"
            '{"name": "host_exec", "arguments": {"command": "Get-Process"}}\n'
            "</tool_call>\n\n"

            "AVAILABLE TOOLS (see TOOL ROUTING for when to use each):\n"
            "sequentialthinking, host_exec, run_script, find_file, read_file, grep_file, find_and_grep, "
            "write_file, append_note, list_network_interfaces, capture_packets, analyze_pcapng, "
            "dns_lookup, ping_sweep, port_scan, http_headers_check, ssl_analysis, cve_lookup, system_info, "
            "encode_decode, hash_identify, crack_hash, finding_create, finding_list, report_generate.\n\n"

            "CRITICAL RULES:\n"
            "- Do NOT invent facts; verify with a tool. Do NOT claim a file was created until write_file succeeded on the target path.\n"
            "- Emit ONE tool call per turn; never repeat an identical call. If a tool fails, investigate with find_file/read_file/grep_file/host_exec before retrying.\n"
            "- In chat mode: NEVER emit [SYSTEM] Task complete, [STATUS], or **Next Steps** prose without a <tool_call> block in the same turn.\n"
            f"- Active session id: {self.session_id}. Prior sessions under workspace/sessions/ — read them only when the user asks for older work.\n"
            "- host_exec is a LAST RESORT; prefer specialized tools (port_scan, dns_lookup, analyze_pcapng, etc.).\n"
        )

        self.ctx_manager.clear_history()
        self.ctx_manager.add_message({"role": "system", "content": prompt})

    # ── Public interface ───────────────────────────────────────────────────

    def clear_history(self):
        """Deprecated: use new_session() to start fresh without deleting prior sessions."""
        self.new_session()

    def new_session(self) -> str:
        """Start a new session id; preserve prior session state and workspace files."""
        self.ctx_manager.save_state()
        previous = self.session_id
        self.session_id = generate_session_id()
        save_active_session(self.session_id, previous=previous)
        self.session_note_paths = ensure_session_layout(self.session_id)
        self.ctx_manager = AgentContextManager(
            mode="autonomous",
            session_id=self.session_id,
            max_total_context=self.max_total_messages,
            max_context_chars=self.max_context_chars,
            max_tool_result_chars=self.max_tool_result_chars,
            max_context_tokens=self.max_context_tokens,
            reserve_generation_tokens=self.reserve_generation_tokens,
            reserve_injection_tokens=self.reserve_injection_tokens,
        )
        self._anchor_query = ""
        self.thinking_engine.reset()
        self.retry_orchestrator.reset()
        self._init_system_prompt()
        return self.session_id

    def request_cancel(self):
        """Signal the running mission to stop after the current step."""
        logger.info("Mission cancellation requested.")
        self._cancel_event.set()

    def export_session(self) -> list[dict]:
        return self.ctx_manager.get_messages()

    def import_session(self, messages: list[dict]):
        self.ctx_manager.set_messages(messages)
        self.ctx_manager.save_state()

    @property
    def messages(self) -> list[dict]:
        """Legacy attribute accessor for console.py compatibility."""
        return self.ctx_manager.get_messages()

    def _add_nudge(self, content: str) -> bool:
        """Add a transient control nudge, skipping exact duplicates.

        Recurrent control signals (goal/deliverable/stall directives) would
        otherwise accumulate in persisted history and inflate every subsequent
        prompt. We drop the add if an identical nudge already exists in the
        recent window; stale nudges are further collapsed during trim_context.
        Returns True if the nudge was appended.
        """
        text = (content or "").strip()
        if not text:
            return False
        recent = self.ctx_manager.get_messages()[-10:]
        for m in recent:
            if m.get("role") == "user" and str(m.get("content", "")).strip() == text:
                return False
        self.ctx_manager.add_message({"role": "user", "content": text})
        return True

    def _artifact_refs(self) -> list[str]:
        """Compact list of on-disk artifact pointers for CURRENT STATE.

        Sourced from structured facts plus the requested deliverables that
        already exist — paths only, no content (recoverable via read_file).
        """
        refs: list[str] = []
        seen: set[str] = set()

        def _add(label: str, value: str) -> None:
            v = (value or "").strip()
            if not v or v in seen:
                return
            seen.add(v)
            refs.append(f"{label}: {v}")

        try:
            from core.facts_store import load_facts

            facts = load_facts(self.session_id)
            pcap = facts.get("pcap", {}) if isinstance(facts, dict) else {}
            _add("pcap", str(pcap.get("path", "")))
            _add("verbose_log", str(pcap.get("verbose_log_file", "")))
        except Exception:
            pass

        try:
            from core.path_catalog import session_context_paths, rel_path

            for label, path in session_context_paths(self.session_id):
                if label in ("login_forms.txt", "pwd.txt") and path.is_file():
                    _add(label, rel_path(path))
        except Exception:
            pass

        return refs[:8]

    # ── Tool execution ─────────────────────────────────────────────────────

    async def _execute_tool(
        self,
        tool_name: str,
        tool_args: dict,
        tools_called: set,
        step_callback=None,
    ) -> tuple[bool, int]:
        """
        Execute one tool call through normalisation, deduplication, dispatch,
        compaction, and audit recording.

        Returns (did_execute: bool, tools_executed_delta: int).
        """
        # Normalise args
        tool_args = ArgumentNormalizer.normalize(tool_name, tool_args)

        if tool_name == "run_script" and tool_args.get("script_path"):
            self._last_script_path = str(tool_args["script_path"])
            set_pip_near(self._last_script_path)
        elif self._pending_script_failure and tool_name == "host_exec":
            set_pip_near(self._pending_script_failure.get("script_path") or self._last_script_path)
        else:
            set_pip_near(None)

        if tool_name == "append_note" and self._pending_script_failure:
            line = str(tool_args.get("line", "")).lower()
            if re.search(
                r"\b(completed successfully|task completed|proceeding with data|transformation completed|"
                r"data fetching and transformation completed)\b",
                line,
                re.I,
            ):
                block_err = (
                    f"Blocked: script '{self._pending_script_failure.get('script_path')}' failed "
                    f"(missing module '{self._pending_script_failure.get('missing_module')}'). "
                    "Install dependency with pip_install_command from the last run_script result, "
                    "then re-run the same script. Do not log false completion."
                )
                if step_callback:
                    step_callback("AGENT_TOOL_CALL", {"tool": tool_name, "args": tool_args})
                    step_callback("AGENT_TOOL_RESULT", {
                        "tool": tool_name,
                        "result": {"success": False, "error": block_err},
                    })
                self.ctx_manager.add_message({
                    "role": "tool",
                    "name": tool_name,
                    "content": json.dumps({"success": False, "error": block_err}),
                })
                return False, 0

        pending = (
            self._active_intent.pending_deliverables(self.workspace_root)
            if self._active_intent else []
        )

        if tool_name == "append_note" and "path" in tool_args:
            from core.session_paths import normalize_note_path
            tool_args = dict(tool_args)
            tool_args["path"] = normalize_note_path(str(tool_args["path"]), self.session_id)
            tool_args.setdefault("session_id", self.session_id)

        if tool_name == "write_file" and "path" in tool_args:
            tool_args = dict(tool_args)
            tool_args.setdefault("session_id", self.session_id)
            if pending:
                tool_args.setdefault("deliverables", pending)

        tool_name, tool_args, redirect_note = ExecutionPolicy.apply(tool_name, tool_args)

        tool_name, tool_args, block_err = WriteGuard.apply(
            tool_name,
            tool_args,
            self._active_intent,
            session_id=self.session_id,
            pending_deliverables=pending,
        )
        if not block_err:
            executed_so_far = (
                getattr(self, "_chat_tools_executed", [])
                if getattr(self, "_chat_goals", None)
                else getattr(self, "_mission_tools_executed", [])
            )
            active_goals = getattr(self, "_chat_goals", None) or getattr(self, "_mission_goals", None)
            plan = getattr(self, "_task_plan", None)
            strategy_note = (
                tool_name == "append_note"
                and plan is not None
                and plan.needs_readaptation()
            )
            tool_name, tool_args, block_err = ChatGoalGuard.apply(
                tool_name, tool_args,
                active_goals,
                executed_so_far,
                strategy_note=strategy_note,
            )
        if block_err:
            if step_callback:
                step_callback("AGENT_TOOL_CALL", {"tool": tool_name, "args": tool_args})
                step_callback("AGENT_TOOL_RESULT", {
                    "tool": tool_name,
                    "result": {"success": False, "error": block_err},
                })
            self.ctx_manager.add_message({
                "role":    "tool",
                "name":    tool_name,
                "content": json.dumps({"success": False, "error": block_err}),
            })
            if self._mission_tracker:
                self._mission_tracker.register(tool_name, {"success": False, "error": block_err}, False, True)
            return False, 0

        if tool_name == "write_file":
            content = str(tool_args.get("content", ""))
            path = str(tool_args.get("path", "")).replace("\\", "/")
            cg = getattr(self, "_chat_goals", None)
            if cg and "crack_hash" in cg.required_tools and "crack_hash" in cg.pending(
                getattr(self, "_chat_tool_events", [])
            ):
                block_err = (
                    f"Blocked: run crack_hash before writing '{path}'. "
                    "Extract hash+salt from http_forms/login_token, then write cracked plaintext."
                )
                if step_callback:
                    step_callback("AGENT_TOOL_CALL", {"tool": tool_name, "args": tool_args})
                    step_callback("AGENT_TOOL_RESULT", {
                        "tool": tool_name,
                        "result": {"success": False, "error": block_err},
                    })
                self.ctx_manager.add_message({
                    "role": "tool",
                    "name": tool_name,
                    "content": json.dumps({"success": False, "error": block_err}),
                })
                return False, 0
            if _is_credential_deliverable(path) and _looks_like_placeholder_file(content):
                block_err = (
                    f"Blocked: {path} must contain REAL values extracted from PCAP/reports "
                    "(not empty, not placeholders like user:password or xmlObj:salt). "
                    "Use credential fields from analyze_pcapng, grep_file on "
                    ".pulse/pcap_logs/verbose_*.txt for xml/salt, or read_file reports."
                )
                # #region agent log
                try:
                    from core.debug_log import debug_log
                    debug_log(
                        "agent.py:_execute_tool",
                        "blocked placeholder credential file",
                        {"path": path, "content_head": content[:120]},
                        "H3",
                    )
                except Exception:
                    pass
                # #endregion
                if step_callback:
                    step_callback("AGENT_TOOL_CALL", {"tool": tool_name, "args": tool_args})
                    step_callback("AGENT_TOOL_RESULT", {
                        "tool": tool_name,
                        "result": {"success": False, "error": block_err},
                    })
                self.ctx_manager.add_message({
                    "role": "tool",
                    "name": tool_name,
                    "content": json.dumps({"success": False, "error": block_err}),
                })
                plan = getattr(self, "_task_plan", None)
                if plan and plan.steps:
                    plan.register_tool(tool_name, {"success": False, "error": block_err}, tool_args)
                    # Readaptation surfaced via plan_block next step, not persisted.
                return False, 0

        # Deduplication
        call_key = f"{tool_name}:{json.dumps(tool_args, sort_keys=True, default=str)}"
        call_hash = hashlib.sha256(call_key.encode()).hexdigest()[:16]

        if call_hash in tools_called:
            logger.info("Skipping duplicate call: %s", tool_name)
            self.ctx_manager.add_message({
                "role":    "tool",
                "name":    tool_name,
                "content": "SKIP: Duplicate call with identical arguments.",
            })
            if self._mission_tracker:
                self._mission_tracker.register(tool_name, "SKIP: duplicate", False, True)
            return False, 0
        tools_called.add(call_hash)

        if step_callback:
            step_callback("AGENT_TOOL_CALL", {"tool": tool_name, "args": tool_args})

        tool_func = self.tools_registry.get(tool_name)
        t_start   = time.monotonic()

        if tool_func:
            try:
                if tool_name == "sequentialthinking":
                    result       = tool_func(tool_args)
                    audit_status = "success"
                    audit_error  = None
                else:
                    result       = await asyncio.to_thread(tool_func, **tool_args)
                    audit_status = "success"
                    audit_error  = None
            except Exception as ex:
                result       = {"success": False, "error": str(ex)}
                audit_status = "error"
                audit_error  = str(ex)
        else:
            result       = {"success": False, "error": f"Tool '{tool_name}' not in registry."}
            audit_status = "error"
            audit_error  = f"Tool '{tool_name}' not found"

        if isinstance(result, dict) and redirect_note:
            result["redirect_note"] = redirect_note

        # Auto-recover missing PCAP path in chat hash/extract workflow.
        if (
            tool_name == "analyze_pcapng"
            and isinstance(result, dict)
            and result.get("success") is False
            and "does not exist" in str(result.get("error", "")).lower()
            and getattr(self, "_chat_goals", None) is not None
        ):
            ff_res = tools.find_file("last_capture.pcapng")
            rec = str(ff_res.get("recommended") or "").strip()
            if rec:
                recover_args = dict(tool_args)
                recover_args["file_path"] = rec
                if step_callback:
                    step_callback("AGENT_TOOL_CALL", {"tool": "analyze_pcapng", "args": recover_args})
                recover = await asyncio.to_thread(tools.analyze_pcapng, **recover_args)
                if step_callback:
                    step_callback("AGENT_TOOL_RESULT", {"tool": "analyze_pcapng", "result": recover})
                if isinstance(recover, dict) and recover.get("success"):
                    result = recover

        # Track per-chat tool success for goal completion semantics.
        if getattr(self, "_chat_goals", None) is not None:
            success_flag = True
            if isinstance(result, dict) and result.get("success") is False:
                success_flag = False
            if (
                tool_name == "write_file"
                and success_flag
                and self._active_intent
                and self._active_intent.deliverables
            ):
                wpath = str(tool_args.get("path", ""))
                if not path_matches_deliverable(wpath, self._active_intent.deliverables):
                    success_flag = False
                    # #region agent log
                    try:
                        from core.debug_log import debug_log
                        debug_log(
                            "agent.py:_execute_tool",
                            "write_file wrong deliverable for goals",
                            {"path": wpath, "expected": self._active_intent.deliverables},
                            "W2",
                        )
                    except Exception:
                        pass
                    # #endregion
            cg = getattr(self, "_chat_goals", None)
            if tool_name == "crack_hash":
                # A terminal crack outcome (found OR exhausted) completes the step;
                # only genuine tool errors (bad args, launcher missing) leave it pending.
                if isinstance(result, dict) and (
                    result.get("success") or result.get("status") == "exhausted"
                ):
                    success_flag = True
            if tool_name == "write_file" and success_flag and cg and "crack_hash" in cg.required_tools:
                content = str(tool_args.get("content", ""))
                done = ChatGoals._successful_names(self._chat_tool_events)
                if "crack_hash" not in done or _looks_like_extraction_draft(content):
                    success_flag = False
                    # #region agent log
                    try:
                        from core.debug_log import debug_log
                        debug_log(
                            "agent.py:_execute_tool",
                            "write_file before crack or extraction draft",
                            {
                                "path": str(tool_args.get("path", "")),
                                "crack_done": "crack_hash" in done,
                                "extraction_draft": _looks_like_extraction_draft(content),
                            },
                            "H5",
                        )
                    except Exception:
                        pass
                    # #endregion
            self._chat_tool_events.append({
                "name": tool_name,
                "success": success_flag,
                "args": dict(tool_args),
            })

        script_hint = self._build_script_failure_hint(tool_name, result, tool_args)
        if script_hint:
            self.ctx_manager.add_message({"role": "user", "content": script_hint})

        failure_hint = self._build_failure_playbook_hint(tool_name, result)
        if failure_hint:
            self.ctx_manager.add_message({
                "role":    "user",
                "content": failure_hint,
            })
        elif not script_hint:
            reflection_hint = self._build_tool_reflection_hint(tool_name, result)
            if reflection_hint:
                self.ctx_manager.add_message({
                    "role":    "user",
                    "content": reflection_hint,
                })
            else:
                grep_hint = self._build_grep_miss_hint(tool_name, result, tool_args)
                if grep_hint:
                    self.ctx_manager.add_message({"role": "user", "content": grep_hint})

        if tool_name == "run_script" and isinstance(result, dict):
            exit_code = result.get("exit_code", 0)
            if exit_code == 0:
                self._pending_script_failure = None
            elif result.get("missing_module"):
                self._pending_script_failure = {
                    "script_path": result.get("script") or tool_args.get("script_path"),
                    "missing_module": result.get("missing_module"),
                    "pip_install_command": result.get("pip_install_command"),
                }
        elif tool_name == "host_exec" and isinstance(result, dict):
            if result.get("exit_code") == 0 and "pip install" in str(tool_args.get("command", "")).lower():
                self._pending_script_failure = None

        duration_ms = int((time.monotonic() - t_start) * 1000)
        result_str  = json.dumps(result, default=str)
        spill_meta = maybe_spill_text(
            self.session_id,
            tool_name,
            result_str,
            threshold_chars=max(16_000, int(self.max_tool_result_chars * 0.75)),
            preview_chars=1800,
        )
        if spill_meta and isinstance(result, dict):
            result["_artifact"] = {
                "artifact_file": spill_meta["artifact_file"],
                "artifact_bytes": spill_meta["artifact_bytes"],
                "artifact_lines": spill_meta["artifact_lines"],
            }
            result["_artifact_note"] = spill_meta["artifact_note"]
            result_str = json.dumps(result, default=str)
        result_hash = hashlib.sha256(result_str.encode()).hexdigest()

        _raw_len = len(result_str)
        result_str = ResultCompactor.compact(tool_name, result_str)

        if tool_name == "analyze_pcapng" and isinstance(result, dict) and result.get("success"):
            analysis = result.get("analysis") or {}
            low = result_str.lower()
            digest_parts: list[str] = []
            if not any(k in low for k in ("login", "password", "xmlobj", "credential")):
                kf = analysis.get("key_fields")
                if kf:
                    digest_parts.append(f"key_fields:\n{str(kf)[:4000]}")
                for key in ("potential_plaintext_credentials", "http_forms"):
                    val = analysis.get(key)
                    if val:
                        digest_parts.append(f"{key}:\n{str(val)[:4000]}")
            # Fold the verbose-log pointer into this single canonical tool message
            # so we do not emit a separate summary message for the same analysis.
            log_file = analysis.get("verbose_log_file")
            pointer = ""
            if log_file:
                log_bytes = analysis.get("verbose_log_bytes", 0)
                pointer = (
                    f"verbose_log ({log_bytes} chars) -> {log_file}\n"
                    f"read_file(path=\"{log_file}\", line_start=1, line_count=100) to inspect; "
                    f"find_and_grep(pattern='xml|Password|Username|616a6178|xmlObj', "
                    f"path_glob='.pulse/pcap_logs/verbose_*.txt', case_insensitive=true, max_files=10)."
                )
            if digest_parts or pointer:
                extra = ""
                if digest_parts:
                    extra += "\n\n[CREDENTIAL DIGEST]\n" + "\n\n".join(digest_parts)
                if pointer:
                    extra += "\n\n[VERBOSE LOG POINTER]\n" + pointer
                result_str = (result_str[:16_000] + extra)[:ResultCompactor.MAX_CHARS]
        elif spill_meta and isinstance(result, dict):
            # Pointer-first compaction for large non-PCAP outputs.
            result_str = json.dumps(
                {
                    "success": result.get("success", True),
                    "artifact_file": spill_meta["artifact_file"],
                    "artifact_bytes": spill_meta["artifact_bytes"],
                    "artifact_lines": spill_meta["artifact_lines"],
                    "artifact_preview": spill_meta["artifact_preview"],
                    "note": spill_meta["artifact_note"],
                },
                default=str,
                indent=2,
            )

        # #region agent log
        if tool_name == "analyze_pcapng":
            try:
                from core.debug_log import debug_log
                _low = result_str.lower()
                debug_log(
                    "agent.py:_execute_tool:compact",
                    "analyze_pcapng result into context",
                    {
                        "raw_len": _raw_len,
                        "compacted_len": len(result_str),
                        "was_compacted": len(result_str) != _raw_len,
                        "has_login_kw": any(k in _low for k in ("login", "password", "xmlobj")),
                    },
                    "C", "run1",
                )
            except Exception:
                pass
        # #endregion

        # #region agent log
        if tool_name in ("grep_file", "find_and_grep"):
            try:
                from core.debug_log import debug_log
                debug_log(
                    "agent.py:_execute_tool:grep",
                    "grep result",
                    {
                        "tool": tool_name,
                        "path": str((tool_args or {}).get("path") or (tool_args or {}).get("path_glob", "")),
                        "pattern": str((tool_args or {}).get("pattern", "")),
                        "case_insensitive": (tool_args or {}).get("case_insensitive"),
                        "match_count": result.get("match_count") if tool_name == "grep_file" else result.get("total_matches"),
                        "files_with_matches": result.get("files_with_matches"),
                    },
                    "A",
                )
            except Exception:
                pass
        # #endregion

        # Audit
        get_audit().record(AuditEntry(
            method=tool_name,
            params=tool_args,
            status=audit_status,
            result_hash=result_hash,
            error=audit_error,
            specialist=self.active_specialist,
            network_mode=self.network_mode,
            duration_ms=duration_ms,
        ))

        if step_callback:
            step_callback("AGENT_TOOL_RESULT", {"tool": tool_name, "result": result})

        if tool_name == "analyze_pcapng" and isinstance(result, dict) and result.get("success"):
            analysis = result.get("analysis", {})
            self._last_pcap_path = str(tool_args.get("file_path") or self._last_pcap_path or "")
            try:
                from core.credential_extract import (
                    build_login_forms_draft,
                    extract_hash_salt_pairs,
                    find_xml_salts,
                    has_login_evidence,
                    pair_hashes_with_salts,
                )
                new_pairs = extract_hash_salt_pairs(analysis)
                if new_pairs:
                    if self._credential_pairs and not any(
                        p.get("salt") for p in self._credential_pairs
                    ):
                        salts = [p.get("salt", "") for p in new_pairs if p.get("salt")]
                        if not salts:
                            blob = "\n".join(
                                str(analysis.get(k, ""))
                                for k in ("key_fields", "packet_summary", "http_index")
                            )
                            salts = find_xml_salts(blob)
                        base = [
                            {
                                "hash": p["hash"],
                                "username": p.get("username", ""),
                                "session_token": p.get("session_token", ""),
                            }
                            for p in self._credential_pairs
                        ] or [
                            {
                                "hash": p["hash"],
                                "username": p.get("username", ""),
                                "session_token": p.get("session_token", ""),
                            }
                            for p in new_pairs
                        ]
                        self._credential_pairs = pair_hashes_with_salts(base, salts)
                    else:
                        self._credential_pairs = new_pairs
                elif self._credential_pairs and not any(
                    p.get("salt") for p in self._credential_pairs
                ):
                    blob = "\n".join(
                        str(analysis.get(k, ""))
                        for k in ("key_fields", "packet_summary", "http_index", "http_forms")
                    )
                    salts = find_xml_salts(blob)
                    if salts:
                        base = [
                            {
                                "hash": p["hash"],
                                "username": p.get("username", ""),
                                "session_token": p.get("session_token", ""),
                            }
                            for p in self._credential_pairs
                        ]
                        self._credential_pairs = pair_hashes_with_salts(base, salts)
                pairs = self._credential_pairs
                if pairs:
                    # #region agent log
                    try:
                        from core.debug_log import debug_log
                        debug_log(
                            "agent.py:_execute_tool",
                            "hash/salt pairs extracted",
                            {"count": len(pairs), "has_salt": any(p.get("salt") for p in pairs)},
                            "H5",
                        )
                    except Exception:
                        pass
                    # #endregion
                if has_login_evidence(analysis):
                    self._pcap_objective_met = True
                    draft = build_login_forms_draft(analysis)
                    if draft:
                        deliverable = "login_forms.txt"
                        if self._active_intent and self._active_intent.deliverables:
                            deliverable = self._active_intent.deliverables[0]
                        # Store as a transient draft injected once (ephemeral), not
                        # persisted to history — avoids a third copy of the same
                        # analysis fields living in the message log.
                        self._pcap_draft = (
                            f"[DRAFT for write_file(path='{deliverable}')] "
                            "from analyze_pcapng — verify xmlObj/salt, edit if needed:\n"
                            f"{draft}"
                        )
                        self._pcap_draft_raw = draft
                        # #region agent log
                        try:
                            from core.debug_log import debug_log
                            debug_log(
                                "agent.py:_execute_tool",
                                "login_forms draft staged (ephemeral)",
                                {"deliverable": deliverable, "draft_len": len(draft)},
                                "H4",
                            )
                        except Exception:
                            pass
                        # #endregion
            except Exception:
                pass
            if analysis.get("extracted_secrets"):
                self._pcap_objective_met = True

        if tool_name == "crack_hash" and isinstance(result, dict) and (
            result.get("success") or result.get("status") == "exhausted"
        ):
            self._crack_results.append(dict(result))
            # #region agent log
            try:
                from core.debug_log import debug_log
                debug_log(
                    "agent.py:_execute_tool",
                    "crack_hash result recorded",
                    {
                        "count": len(self._crack_results),
                        "has_password": bool(result.get("password")),
                        "status": result.get("status"),
                    },
                    "H5",
                )
            except Exception:
                pass
            # #endregion

        if tool_name == "analyze_pcapng" and isinstance(result, dict) and result.get("success"):
            analysis = result.get("analysis", {})
            if analysis.get("extracted_secrets"):
                pass  # handled above
            creds = str(analysis.get("potential_plaintext_credentials", ""))
            if creds and re.search(r"(login|password|authorization|credential)", creds, re.I):
                self._pcap_objective_met = True
            parts = []
            _SECTION_CAP = 8_000
            _TOTAL_CAP = 20_000

            # Prefer targeted key_fields extraction (compact, useful)
            key_fields = analysis.get("key_fields")
            if key_fields:
                parts.append(f"### key_fields\n{key_fields[:_SECTION_CAP]}")

            for key in (
                "potential_plaintext_credentials",
                "http_forms",
                "http_index",
                "packet_summary",
                "protocol_hierarchy",
            ):
                val = analysis.get(key)
                if val:
                    parts.append(f"### {key}\n{val[:_SECTION_CAP]}")

            # Reference the log file so the agent can read_file in chunks
            log_file = analysis.get("verbose_log_file")
            if log_file:
                log_bytes = analysis.get("verbose_log_bytes", 0)
                parts.append(
                    f"### verbose_log\n"
                    f"Full verbose decode ({log_bytes} chars) saved to:\n"
                    f"  {log_file}\n"
                    f"Use read_file(path=\"{log_file}\", line_start=1, line_count=100) to inspect."
                )

            if parts:
                joined = "\n\n".join(parts)
                self._last_pcap_summary = joined[:_TOTAL_CAP]

        self.ctx_manager.add_message({
            "role":    "tool",
            "name":    tool_name,
            "content": result_str,
        })

        # Persist facts from successful recon/pcap tools.
        try:
            if isinstance(result, dict):
                update_from_tool(self.session_id, tool_name, result, tool_args)
        except Exception:
            pass

        if self._mission_tracker:
            self._mission_tracker.register(tool_name, result, True, False)

        plan = getattr(self, "_task_plan", None)
        if plan and plan.steps:
            plan.register_tool(tool_name, result, tool_args)
            try:
                if plan.all_done:
                    clear_plan_state(self.session_id)
                else:
                    save_plan_state(self.session_id, plan)
            except Exception:
                pass
            if plan.needs_readaptation():
                note = plan.last_failure or "step failed"
                plan.record_strategy(note)
                cur = plan.current_step
                if cur:
                    plan.append_scratchpad(self.session_id, cur.id, note)
                # Readaptation guidance is injected ephemerally via plan_block on
                # the next step, not appended to persisted history.

        # Credential-extraction guidance is carried by the single tool message
        # (CREDENTIAL DIGEST / VERBOSE LOG POINTER) and the plan_block injection,
        # so no separate persisted nudge is emitted here.

        if tool_name == "read_file" and isinstance(result, dict) and result.get("success"):
            if re.search(r"(login|password|xmlobj)", str(result.get("content", "")), re.I):
                self._pcap_objective_met = True

        success_exec = not (isinstance(result, dict) and result.get("success") is False)

        # Phase 2 STRUCTURED UPDATE: refresh volatile working memory + last-tool
        # head so the NEXT CURRENT STATE reflects this step, instead of appending
        # narrative user messages to the persisted history.
        try:
            ok_word = "ok" if success_exec else "FAILED"
            self._last_tool_head = f"{tool_name} -> {ok_word}: {result_str[:380]}"
            wm_failure = None
            wm_next = None
            if plan and plan.steps:
                pc = plan.compact()
                wm_next = pc.get("next_action") or None
                if plan.needs_readaptation():
                    wm_failure = pc.get("last_failure") or None
            self._working_memory.update(
                observation=self._last_tool_head,
                next_action=wm_next,
                failure=("" if success_exec and not wm_failure else wm_failure),
            )
            save_working_memory(self.session_id, self._working_memory)
        except Exception:
            pass

        return success_exec, (1 if success_exec else 0)

    # ── Mission loop ───────────────────────────────────────────────────────

    async def run_mission(self, user_prompt: str, step_callback=None) -> str:
        """
        Full autonomous ReAct loop.

        Features:
        - Context trimming every step
        - Parser reflection when no tool call is found
        - Duplicate deduplication
        - MIN_TOOLS_BEFORE_COMPLETE guard
        - Cancellation support
        - Structured final synthesis
        """
        # Ensure we have a system prompt
        if not self.ctx_manager.has_system():
            self._init_system_prompt()

        self._cancel_event.clear()
        self._anchor_query = user_prompt
        self._active_intent = TaskIntentExtractor.parse(user_prompt)
        self.ctx_manager.add_message({"role": "user", "content": user_prompt})
        self._mission_goals = ChatGoalRegistry.match_message(user_prompt)
        self._mission_tools_executed = []
        self._mission_tracker = MissionProgressTracker(user_prompt)
        mission_nudges = 0
        max_mission_nudges = 4
        recent_result_heads: list[str] = []
        if self._mission_goals and self._mission_goals.context_directive():
            self.ctx_manager.add_message({
                "role": "user",
                "content": self._mission_goals.context_directive(),
            })

        tools_executed: int  = 0
        tools_called: set    = set()
        consecutive_empty: int = 0
        final_answer: str    = ""

        for step in range(self.max_steps):
            if self._cancel_event.is_set():
                final_answer = "[Mission cancelled by user.]"
                break

            # Trim context before each LLM call
            self.ctx_manager.trim_context()

            if step_callback:
                step_callback(
                    "AGENT_STATUS",
                    f"Step {step + 1}/{self.max_steps} | Tools: {tools_executed} | Thinking…",
                )

            # LLM call
            try:
                response = await self.adapter.chat(
                    messages=self.ctx_manager.get_messages(),
                    tools_schema=tools.TOOLS_SCHEMA,
                    task_intent=self._active_intent,
                    anchor_query=self._anchor_query,
                )
            except Exception as e:
                err = f"Ollama error: {e}"
                if step_callback:
                    step_callback("ERROR", err)
                return err

            msg      = response.get("message", {})
            content  = msg.get("content", "") or ""
            raw_tcs  = msg.get("tool_calls", [])

            # Parse output
            _, reasoning, tool_calls = self.parser.process_llm_output(msg)

            # Persist assistant message with tool calls so the prompt template renders them
            assistant_msg = {"role": "assistant", "content": content}
            if tool_calls:
                assistant_msg["tool_calls"] = tool_calls
            self.ctx_manager.add_message(assistant_msg)

            # Emit reasoning
            if reasoning and step_callback:
                step_callback("AGENT_THOUGHT", reasoning)
            elif content and not tool_calls and step_callback:
                step_callback("AGENT_TEXT", content)

            # #region agent log
            try:
                from core.debug_log import debug_log
                debug_log(
                    "agent.py:run_mission:step",
                    "step decision",
                    {
                        "step": step,
                        "tools_executed": tools_executed,
                        "has_mission_complete": "MISSION_COMPLETE" in content,
                        "n_tool_calls": len(tool_calls) if tool_calls else 0,
                        "tool_names": [
                            (tc.get("function", tc) or {}).get("name", tc.get("name", ""))
                            for tc in (tool_calls or [])
                        ],
                        "content_head": content[:300],
                    },
                    "A", "run1",
                )
            except Exception:
                pass
            # #endregion

            # MISSION_COMPLETE guard
            if "MISSION_COMPLETE" in content:
                objective_ok = self._mission_tracker.objective_satisfied() if self._mission_tracker else True
                substantive_ok = (
                    not self._mission_tracker
                    or self._mission_tracker.substantive_tools >= self.MIN_SUBSTANTIVE_BEFORE_COMPLETE
                )
                if (
                    tools_executed >= self.MIN_TOOLS_BEFORE_COMPLETE
                    and objective_ok
                    and substantive_ok
                ):
                    # #region agent log
                    try:
                        from core.debug_log import debug_log, log_completion_exit
                        log_completion_exit(
                            "mission",
                            "MISSION_COMPLETE accepted",
                            step=step,
                            tools_executed=tools_executed,
                            objective_ok=objective_ok,
                            hypothesis_id="B",
                        )
                        debug_log(
                            "agent.py:run_mission:complete_accepted",
                            "MISSION_COMPLETE accepted",
                            {"step": step, "tools_executed": tools_executed},
                            "A", "run1",
                        )
                    except Exception:
                        pass
                    # #endregion
                    final_answer = await self._final_synthesis()
                    if step_callback:
                        step_callback("MISSION_COMPLETED", final_answer)
                    break
                else:
                    # Not enough tools run — reject and keep going
                    self._add_nudge(
                        f"[SYSTEM] MISSION_COMPLETE rejected — tools_executed={tools_executed}, "
                        f"minimum={self.MIN_TOOLS_BEFORE_COMPLETE}, "
                        f"substantive={getattr(self._mission_tracker, 'substantive_tools', 0)}, "
                        f"substantive_minimum={self.MIN_SUBSTANTIVE_BEFORE_COMPLETE}, "
                        f"objective_satisfied={objective_ok}. "
                        "Continue your investigation and produce evidence before completion."
                    )
                    continue

            if tool_calls:
                consecutive_empty = 0
                self.retry_orchestrator.reset()
                for tc in tool_calls:
                    func     = tc.get("function", tc)
                    name     = func.get("name", tc.get("name", ""))
                    args = func.get("arguments", tc.get("arguments", {}))
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except (json.JSONDecodeError, TypeError):
                            args = {}
                    if not isinstance(args, dict):
                        args = {}

                    did_exec, delta = await self._execute_tool(
                        name, args, tools_called, step_callback
                    )
                    tools_executed += delta
                    if did_exec:
                        self._mission_tools_executed.append(name)
                        if isinstance(self.ctx_manager.get_messages()[-1].get("content", ""), str):
                            recent_result_heads.append(self.ctx_manager.get_messages()[-1]["content"][:240])
                        if len(recent_result_heads) > 5:
                            recent_result_heads = recent_result_heads[-5:]

                pending_goals = self._mission_goals.pending(self._mission_tools_executed) if self._mission_goals else []
                if pending_goals and mission_nudges < max_mission_nudges:
                    mission_nudges += 1
                    if mission_nudges >= max_mission_nudges and "analyze_pcapng" in pending_goals and self._mission_goals:
                        boot = await self._bootstrap_pcap_analysis(
                            self._mission_goals, tools_called, step_callback
                        )
                        if boot:
                            self._mission_tools_executed.extend(boot)
                    if self._mission_goals and self._mission_goals.pending(self._mission_tools_executed):
                        self._add_nudge(
                            self._mission_goals.nudge_text(
                                self._mission_goals.pending(self._mission_tools_executed)
                            )
                        )

                if self._mission_tracker and self._mission_tracker.needs_stall_recovery():
                    self._add_nudge(self._mission_tracker.stall_directive())
                    if self.mission_evaluator and MissionEvaluator.should_run(user_prompt):
                        try:
                            eval_data = await self.mission_evaluator.evaluate(
                                user_prompt,
                                self._mission_tools_executed,
                                recent_result_heads,
                                self._mission_tracker.objective_satisfied(),
                            )
                            hint = str(eval_data.get("hint", "")).strip()
                            if hint:
                                self._add_nudge(f"[SYSTEM EVALUATOR] {hint}")
                        except Exception:
                            pass
            else:
                # No tool call — attempt parser reflection
                consecutive_empty += 1

                reflection = self.retry_orchestrator.parser_reflection(
                    content, self.parser, session_id=self.session_id
                )

                if reflection:
                    consecutive_empty = 0
                    logger.info("Parser reflection triggered (step %d)", step)
                    if step_callback:
                        step_callback("AGENT_THOUGHT", "[Parser reflection — self-correcting…]")

                    # Ensure the LLM sees its own faked tool call
                    last_msg = self.ctx_manager.get_messages()[-1]
                    if last_msg.get("role") == "assistant":
                        if "tool_calls" not in last_msg:
                            last_msg["tool_calls"] = []
                        last_msg["tool_calls"].append(reflection)

                    await self._execute_tool(
                        reflection["function"]["name"],
                        reflection["function"]["arguments"],
                        tools_called,
                        step_callback,
                    )
                else:
                    # Hard stall nudge — keep looping instead of exiting early
                    nudge = (
                        "[SYSTEM DIRECTIVE] You are stalling. "
                        "Execute a technical tool NOW — use sequentialthinking to plan, "
                        "then immediately call a recon or execution tool. "
                        "Do NOT produce prose or declare MISSION_COMPLETE yet."
                    )
                    self._add_nudge(nudge)
                    try:
                        from core.debug_log import log_completion_exit
                        log_completion_exit(
                            "mission",
                            "stall nudge (continuing)",
                            step=step,
                            tools_executed=tools_executed,
                            hypothesis_id="C",
                        )
                    except Exception:
                        pass
                    continue

                # If still conversational after a few empty turns on step 0 only, return early
                if consecutive_empty >= 3 and step == 0:
                    try:
                        from core.debug_log import log_completion_exit
                        log_completion_exit(
                            "mission",
                            "early empty step0 exit",
                            step=step,
                            tools_executed=tools_executed,
                            hypothesis_id="C",
                        )
                    except Exception:
                        pass
                    final_answer = content
                    break

        # Hit step limit — synthesise
        if not final_answer:
            try:
                from core.debug_log import log_completion_exit
                log_completion_exit(
                    "mission",
                    "max_steps synthesis",
                    step=step if "step" in locals() else 0,
                    tools_executed=tools_executed if "tools_executed" in locals() else 0,
                    hypothesis_id="E",
                )
            except Exception:
                pass
            final_answer = await self._final_synthesis()
            if step_callback:
                step_callback("MISSION_COMPLETED", final_answer)

        # Memory logging
        try:
            from core.memory import log_daily_execution
            steps_executed = (step + 1) if "step" in locals() else 0
            f_count = len(tools.finding_list().get("findings", []))
            log_daily_execution(
                session_id=self.session_id,
                specialist=self.active_specialist,
                prompt=user_prompt,
                steps_count=steps_executed,
                findings_count=f_count,
                outcome=final_answer
            )
        except Exception:
            pass

        self.ctx_manager.save_state()
        self._active_intent = None
        self._anchor_query = ""
        self._mission_goals = None
        self._mission_tools_executed = []
        self._mission_tracker = None
        return final_answer

    # ── Chat turn (interactive) ────────────────────────────────────────────

    async def chat_turn(self, message: str, step_callback=None) -> str:
        """
        Single-turn interactive chat with optional tool use.
        Returns the assistant's final text response.
        """
        if not self.ctx_manager.has_system():
            self._init_system_prompt()

        def log_chat_mem(outcome_val: str, steps_val: int):
            try:
                from core.memory import log_daily_execution
                f_count = len(tools.finding_list().get("findings", []))
                log_daily_execution(
                    session_id=self.session_id,
                    specialist=self.active_specialist,
                    prompt=message,
                    steps_count=steps_val,
                    findings_count=f_count,
                    outcome=outcome_val
                )
            except Exception:
                pass

        raw_message = message
        # Detect confirmation phrases and add execution directive
        if any(w in message.lower() for w in
               ["yes", "ok", "do it", "go ahead", "execute", "proceed", "run it"]):
            message += "\n\n[SYSTEM DIRECTIVE: User confirmed. Execute the tool NOW. No prose.]"

        self.retry_orchestrator.reset()
        self._anchor_query = raw_message
        self.parser.set_user_context(raw_message)
        self._active_intent = TaskIntentExtractor.parse(message)
        self._chat_tool_events = []
        self._credential_pairs = []
        self._crack_results = []
        self._last_pcap_path = None

        deliverable_hint = ""
        if self._active_intent.deliverables:
            deliverable_hint = (
                f"Required deliverable(s): {', '.join(self._active_intent.deliverables)}. "
                "Write each with write_file before any progress notes.\n"
            )

        chat_directive = (
            "[CHAT MODE] Focus ONLY on the user's request below. "
            "Do NOT declare MISSION_COMPLETE or generate engagement/final reports. "
            "Do NOT run network recon tools unless explicitly requested. "
            f"Use append_note on `{plan_note_rel(self.session_id)}` for progress — never write_file for status lines. "
            "sequentialthinking is optional in chat (max one planning thought); prefer action tools. "
            "Complete the user's task before stopping — append_note alone is not completion. "
            f"{deliverable_hint}\n"
        )
        self.ctx_manager.add_message({"role": "user", "content": chat_directive + message})

        chat_goals = ChatGoalRegistry.match_message(message)
        if not chat_goals:
            chat_goals = ChatGoalRegistry.match_session(
                self.ctx_manager.get_messages(), message
            )
        self._chat_goals = chat_goals
        self._last_pcap_summary: str | None = None
        self._pcap_objective_met: bool = False
        self._task_plan = load_plan_state(self.session_id) or TaskPlanTracker(message)
        # Phase 2: load volatile working memory and reset the per-turn tool head.
        self._working_memory = load_working_memory(self.session_id)
        self._last_tool_head = ""
        self._rehydrate_credential_pairs()

        if chat_goals and chat_goals.context_directive():
            self.ctx_manager.add_message({
                "role": "user",
                "content": chat_goals.context_directive(),
            })

        goal_nudges = 0
        max_goal_nudges = 4
        evaluator_nudges = 0
        max_evaluator_nudges = 1
        crack_bootstrap_attempted = False

        # #region agent log
        try:
            from core.debug_log import debug_log
            debug_log(
                "agent.py:chat_turn",
                "chat goals",
                {
                    "goals": chat_goals.label if chat_goals else None,
                    "required": chat_goals.required_tools if chat_goals else [],
                },
                "F",
            )
        except Exception:
            pass
        # #endregion

        tools_called: set = set()
        tools_executed_names: list[str] = []
        paths_written: list[str] = []
        deliverable_nudges = 0
        consecutive_no_tool = 0
        self._chat_tools_executed: list[str] = []

        for step in range(12):
            self._chat_tools_executed = list(tools_executed_names)

            # Phase 2: one canonical CURRENT STATE injection replaces the separate
            # session snippet + plan status blocks. Rebuilt each step so it always
            # reflects the latest facts/plan/working-memory; never persisted to
            # history. The long checklist lives only on disk (plan_state).
            plan_compact = self._task_plan.compact() if self._task_plan.steps else None
            draft = ""
            if self._pcap_draft:
                draft = self._pcap_draft
                self._pcap_draft = None
            current_state = build_current_state(
                mission=raw_message,
                plan=plan_compact,
                working_memory=self._working_memory,
                last_tool_result=self._last_tool_head,
                draft=draft,
                facts_block=summarize_facts(self.session_id, max_chars=500),
                artifact_refs=self._artifact_refs(),
            )

            self.ctx_manager.trim_context()
            # #region agent log
            try:
                from core.debug_log import debug_log
                debug_log(
                    "agent.py:chat_turn",
                    "ollama step",
                    {
                        "step": step,
                        "tools": list(tools_executed_names),
                        "pending_goals": chat_goals.pending(self._chat_tool_events) if chat_goals else [],
                        "task_done": self._task_plan.all_done if self._task_plan.steps else None,
                        "needs_readapt": self._task_plan.needs_readaptation() if self._task_plan.steps else False,
                    },
                    "H1",
                )
            except Exception:
                pass
            # #endregion
            response = await self.adapter.chat(
                messages=self.ctx_manager.messages_for_llm(self.history_window_turns),
                tools_schema=tools.TOOLS_SCHEMA,
                task_intent=self._active_intent,
                anchor_query=self._anchor_query,
                current_state=current_state or None,
            )

            msg     = response.get("message", {})
            content = msg.get("content", "") or ""
            if isinstance(content, str) and content.strip().startswith("ERROR: Ollama unreachable."):
                # #region agent log
                try:
                    from core.debug_log import debug_log
                    debug_log(
                        "agent.py:chat_turn",
                        "llm unreachable fallback",
                        {
                            "step": step,
                            "chat_goal": chat_goals.label if chat_goals else None,
                            "tools_so_far": list(tools_executed_names),
                        },
                        "L1",
                    )
                except Exception:
                    pass
                # #endregion
                pending_now = chat_goals.pending(self._chat_tool_events) if chat_goals else []
                recovered = False
                if chat_goals and any(t in pending_now for t in ("read_file", "analyze_pcapng")):
                    boot = await self._bootstrap_pcap_analysis(chat_goals, tools_called, step_callback)
                    if boot:
                        tools_executed_names.extend(boot)
                        recovered = True
                elif chat_goals and "find_and_grep" in pending_now:
                    boot = await self._bootstrap_verbose_grep(tools_called, step_callback)
                    if boot:
                        tools_executed_names.extend(boot)
                        recovered = True
                elif chat_goals and "crack_hash" in pending_now and self._credential_pairs:
                    boot = await self._bootstrap_crack_hash(
                        chat_goals, tools_called, step_callback
                    )
                    if boot:
                        tools_executed_names.extend(boot)
                        recovered = True
                elif (
                    chat_goals
                    and "write_file" in pending_now
                    and self._crack_results
                    and self._credential_pairs
                ):
                    boot = await self._bootstrap_write_cracked(
                        chat_goals, tools_called, step_callback
                    )
                    if boot:
                        tools_executed_names.extend(boot)
                        recovered = True
                if recovered:
                    self._chat_tools_executed = list(tools_executed_names)
                    self._add_nudge(
                        "[SYSTEM] LLM temporarily unavailable; continued with deterministic tool fallback."
                    )
                    continue
            _, reasoning, tool_calls = self.parser.process_llm_output(msg)

            assistant_msg = {"role": "assistant", "content": content}
            if tool_calls:
                assistant_msg["tool_calls"] = tool_calls
            self.ctx_manager.add_message(assistant_msg)

            if reasoning and step_callback:
                step_callback("AGENT_THOUGHT", reasoning)
            elif content and not tool_calls and step_callback:
                step_callback("AGENT_TEXT", content.split("```")[0].strip())

            if tool_calls:
                consecutive_no_tool = 0
                batch_executed = False
                batch_only_notes = bool(tool_calls)
                for tc in tool_calls:
                    func = tc.get("function", tc)
                    name = func.get("name", tc.get("name", ""))
                    args = func.get("arguments", tc.get("arguments", {}))
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except (json.JSONDecodeError, TypeError):
                            args = {}
                    if not isinstance(args, dict):
                        args = {}
                    if name != "append_note":
                        batch_only_notes = False
                    did_exec, _ = await self._execute_tool(name, args, tools_called, step_callback)
                    if did_exec:
                        batch_executed = True
                        tools_executed_names.append(name)
                        if name == "write_file" and args.get("path"):
                            paths_written.append(str(args["path"]).replace("\\", "/"))

                self._chat_tools_executed = list(tools_executed_names)

                pending_goals = chat_goals.pending(self._chat_tool_events) if chat_goals else []
                if (
                    chat_goals
                    and "crack_hash" in pending_goals
                    and self._credential_pairs
                    and not self._crack_hash_succeeded()
                    and not crack_bootstrap_attempted
                    and (not batch_executed or batch_only_notes)
                ):
                    crack_bootstrap_attempted = True
                    # #region agent log
                    try:
                        from core.debug_log import debug_log
                        debug_log(
                            "agent.py:chat_turn",
                            "append_note stall → crack bootstrap",
                            {
                                "step": step,
                                "batch_executed": batch_executed,
                                "batch_only_notes": batch_only_notes,
                                "pairs": len(self._credential_pairs),
                            },
                            "L2",
                        )
                    except Exception:
                        pass
                    # #endregion
                    boot = await self._bootstrap_crack_hash(
                        chat_goals, tools_called, step_callback
                    )
                    if boot:
                        tools_executed_names.extend(boot)
                        self._chat_tools_executed = list(tools_executed_names)
                        self._add_nudge(
                            "[SYSTEM] Progress notes blocked — ran crack_hash bootstrap with extracted hash/salt pairs."
                        )
                        continue

                # Do not exit immediately after the last required tool — allow further ReAct steps.

                pending = self._active_intent.pending_deliverables(self.workspace_root)
                if pending and deliverable_nudges < 2:
                    deliverable_nudges += 1
                    self._add_nudge(
                        f"[SYSTEM] Deliverable not on disk yet: {pending[0]}. "
                        "Extract real content from PCAP/reports first, then write_file "
                        "(no placeholders like user:password)."
                    )

                pending_goals = chat_goals.pending(self._chat_tool_events) if chat_goals else []
                if pending_goals and goal_nudges < max_goal_nudges:
                    goal_nudges += 1
                    self._add_nudge(chat_goals.nudge_text(pending_goals))
                    continue

                if (
                    chat_goals
                    and "write_file" in (chat_goals.pending(self._chat_tool_events) or [])
                    and self._crack_results
                    and self._credential_pairs
                    and self._active_intent
                    and self._active_intent.deliverables
                ):
                    boot = await self._bootstrap_write_cracked(
                        chat_goals, tools_called, step_callback
                    )
                    if boot:
                        tools_executed_names.extend(boot)
                        paths_written.append(
                            str(self._active_intent.deliverables[0]).replace("\\", "/")
                        )
                        self._chat_tools_executed = list(tools_executed_names)
                        continue

                if self._task_plan.steps and self._task_plan.needs_readaptation():
                    if self.mission_evaluator and evaluator_nudges < max_evaluator_nudges:
                        evaluator_nudges += 1
                        try:
                            eval_data = await self.mission_evaluator.evaluate(
                                message,
                                tools_executed_names,
                                [self.ctx_manager.get_messages()[-1].get("content", "")[:240]],
                                self._task_plan.all_done,
                            )
                            hint = str(eval_data.get("hint", "")).strip()
                            if hint:
                                self._task_plan.record_strategy(hint)
                                cur = self._task_plan.current_step
                                if cur:
                                    self._task_plan.append_scratchpad(self.session_id, cur.id, hint)
                        except Exception:
                            pass
                    # Readaptation/evaluator guidance is surfaced via plan_block on
                    # the next step (strategy notes + last failure), not persisted.
                    continue
            else:
                consecutive_no_tool += 1
                from core.intent_salvage import (
                    hard_action_nudge,
                    looks_like_prose_stall,
                    salvage_intent_tool_call,
                )

                if looks_like_prose_stall(content):
                    # #region agent log
                    try:
                        from core.debug_log import debug_log
                        debug_log(
                            "agent.py:chat_turn",
                            "prose stall detected",
                            {"step": step, "content_head": content[:200]},
                            "E",
                        )
                    except Exception:
                        pass
                    # #endregion
                    intent_call = salvage_intent_tool_call(
                        content, message, session_id=self.session_id
                    )
                    if intent_call:
                        iname = intent_call["function"]["name"]
                        iargs = intent_call["function"]["arguments"]
                        did_exec, _ = await self._execute_tool(
                            iname, iargs, tools_called, step_callback
                        )
                        if did_exec:
                            tools_executed_names.append(iname)
                            self.retry_orchestrator.reset()
                        self._chat_tools_executed = list(tools_executed_names)
                        continue
                    self._add_nudge(hard_action_nudge(message, self.session_id))
                    continue

                salvage = self.parser.salvage_tool_call(content, user_context=message)
                if not salvage:
                    intent_call = salvage_intent_tool_call(
                        content, message, session_id=self.session_id
                    )
                    if intent_call:
                        salvage = intent_call
                pending_goals = chat_goals.pending(self._chat_tool_events) if chat_goals else []

                if salvage and salvage["function"]["name"] != "sequentialthinking":
                    sname = salvage["function"]["name"]
                    if pending_goals and sname == "append_note" and "analyze_pcapng" in pending_goals:
                        salvage = None
                    else:
                        sargs = salvage["function"]["arguments"]
                        did_exec, _ = await self._execute_tool(
                            sname, sargs, tools_called, step_callback
                        )
                        if did_exec:
                            tools_executed_names.append(sname)
                            if sname == "write_file" and sargs.get("path"):
                                paths_written.append(str(sargs["path"]).replace("\\", "/"))
                        self._chat_tools_executed = list(tools_executed_names)
                        continue

                if pending_goals and goal_nudges < max_goal_nudges:
                    goal_nudges += 1
                    # #region agent log
                    try:
                        from core.debug_log import debug_log
                        debug_log(
                            "agent.py:chat_turn",
                            "goal nudge",
                            {"step": step, "pending": pending_goals, "nudge": goal_nudges},
                            "F",
                        )
                    except Exception:
                        pass
                    # #endregion
                    if goal_nudges >= max_goal_nudges and "analyze_pcapng" in pending_goals:
                        boot = await self._bootstrap_pcap_analysis(
                            chat_goals, tools_called, step_callback
                        )
                        if boot:
                            tools_executed_names.extend(boot)
                            pending_goals = chat_goals.pending(self._chat_tool_events)
                    elif (
                        goal_nudges >= max_goal_nudges
                        and chat_goals
                        and "find_and_grep" not in tools_executed_names
                        and (
                            "find_and_grep" in pending_goals
                            or "grep_file" in tools_executed_names
                        )
                    ):
                        boot = await self._bootstrap_verbose_grep(tools_called, step_callback)
                        if boot:
                            tools_executed_names.extend(boot)
                            pending_goals = chat_goals.pending(self._chat_tool_events)
                    elif goal_nudges >= max_goal_nudges and "crack_hash" in pending_goals:
                        boot = await self._bootstrap_crack_hash(
                            chat_goals, tools_called, step_callback
                        )
                        if boot:
                            tools_executed_names.extend(boot)
                            pending_goals = chat_goals.pending(self._chat_tool_events)
                    elif (
                        goal_nudges >= max_goal_nudges
                        and "write_file" in pending_goals
                        and self._crack_results
                    ):
                        boot = await self._bootstrap_write_cracked(
                            chat_goals, tools_called, step_callback
                        )
                        if boot:
                            tools_executed_names.extend(boot)
                            pending_goals = chat_goals.pending(self._chat_tool_events)
                    if chat_goals.pending(self._chat_tool_events):
                        self._add_nudge(
                            chat_goals.nudge_text(chat_goals.pending(self._chat_tool_events))
                        )
                        continue

                if content and ("?" in content or "¿" in content):
                    try:
                        from core.debug_log import log_completion_exit
                        log_completion_exit(
                            "chat",
                            "assistant question early return",
                            step=step,
                            tools_executed=len(tools_executed_names),
                            chat_goals_label=chat_goals.label if chat_goals else "",
                            hypothesis_id="D",
                        )
                    except Exception:
                        pass
                    log_chat_mem(content, step + 1)
                    self.ctx_manager.save_state()
                    intent_snapshot = self._active_intent
                    self._active_intent = None
                    return self._enforce_deliverables_guard(
                        paths_written, intent_snapshot, self.workspace_root,
                        orig_result=content,
                        tools_executed=tools_executed_names,
                    )
                if chat_goals and chat_goals.is_pcap_goal() and consecutive_no_tool >= 2:
                    pcap_depth = sum(
                        1 for t in tools_executed_names if t in ("analyze_pcapng", "read_file")
                    )
                    if pcap_depth < 2 or not getattr(self, "_pcap_objective_met", False):
                        log_path = None
                        if self._last_pcap_summary:
                            m = re.search(r"(?:\.pulse[/\\]pcap_logs[/\\][^\s\"']+\.txt)", self._last_pcap_summary)
                            if m:
                                log_path = m.group(0).replace("\\", "/")
                        nudge = (
                            "[SYSTEM DIRECTIVE] PCAP workflow incomplete. "
                            "Do NOT summarize yet. Next action MUST be one of:\n"
                            "1) analyze_pcapng with verbose=true and a narrower filter, OR\n"
                            "2) read_file on the verbose_log_file in chunks."
                        )
                        if log_path:
                            nudge += f'\nExample: read_file(path="{log_path}", line_start=1, line_count=80)'
                        self._add_nudge(nudge)
                        try:
                            from core.debug_log import log_completion_exit
                            log_completion_exit(
                                "chat",
                                "pcap depth nudge",
                                step=step,
                                tools_executed=len(tools_executed_names),
                                chat_goals_label=chat_goals.label,
                                hypothesis_id="F",
                            )
                        except Exception:
                            pass
                        continue

                if chat_goals and chat_goals.may_end_turn(
                    self._chat_tool_events,
                    step,
                    objective_met=getattr(self, "_pcap_objective_met", False),
                ) and self._task_plan.may_complete_turn(tools_executed_names, step):
                    try:
                        from core.debug_log import log_completion_exit
                        log_completion_exit(
                            "chat",
                            "may_end_turn break",
                            step=step,
                            tools_executed=len(tools_executed_names),
                            chat_goals_label=chat_goals.label,
                            pending_goals=chat_goals.pending(self._chat_tool_events),
                            hypothesis_id="A",
                        )
                    except Exception:
                        pass
                    break
                reflection = self.retry_orchestrator.parser_reflection(
                    content, self.parser, session_id=self.session_id
                )
                rname = reflection.get("function", {}).get("name", "") if reflection else ""
                if reflection and rname == "sequentialthinking":
                    if (
                        looks_like_prose_stall(content)
                        or (chat_goals and "sequentialthinking" in chat_goals.blocked_tools)
                        or all(t == "sequentialthinking" for t in tools_executed_names)
                    ):
                        intent_call = salvage_intent_tool_call(
                            content, message, session_id=self.session_id
                        )
                        reflection = intent_call
                        rname = reflection.get("function", {}).get("name", "") if reflection else ""
                if reflection and chat_goals and chat_goals.is_pcap_goal():
                    if rname == "sequentialthinking" and consecutive_no_tool >= 3:
                        reflection = None
                if reflection:
                    # Ensure the LLM sees its own faked tool call
                    last_msg = self.ctx_manager.get_messages()[-1]
                    if last_msg.get("role") == "assistant":
                        if "tool_calls" not in last_msg:
                            last_msg["tool_calls"] = []
                        last_msg["tool_calls"].append(reflection)

                    rname = reflection["function"]["name"]
                    rargs = reflection["function"]["arguments"]
                    did_exec, _ = await self._execute_tool(
                        rname, rargs, tools_called, step_callback
                    )
                    if did_exec and rname != "sequentialthinking":
                        tools_executed_names.append(rname)
                        if rname == "write_file" and rargs.get("path"):
                            paths_written.append(str(rargs["path"]).replace("\\", "/"))
                    elif did_exec and rname == "sequentialthinking":
                        self._add_nudge(hard_action_nudge(message, self.session_id))
                    self._chat_tools_executed = list(tools_executed_names)
                    continue
                else:
                    pending_now = chat_goals.pending(self._chat_tool_events) if chat_goals else []
                    # Max reflections or no salvage — force action bootstrap for search/credential work
                    if (
                        not reflection
                        and salvage_intent_tool_call(message, message, session_id=self.session_id)
                        and "find_and_grep" not in tools_executed_names
                    ):
                        boot = await self._bootstrap_verbose_grep(tools_called, step_callback)
                        if boot:
                            tools_executed_names.extend(boot)
                            self._add_nudge(hard_action_nudge(message, self.session_id))
                            continue
                    if pending_now:
                        # #region agent log
                        try:
                            from core.debug_log import debug_log
                            debug_log(
                                "agent.py:chat_turn",
                                "blocked early exit — pending goals",
                                {"step": step, "pending": pending_now, "goal_nudges": goal_nudges},
                                "D",
                            )
                        except Exception:
                            pass
                        # #endregion
                        if chat_goals:
                            self._add_nudge(chat_goals.nudge_text(pending_now))
                        else:
                            self._add_nudge(
                                "[SYSTEM] Task incomplete — emit a tool call instead of summarizing."
                            )
                        continue
                    if step < 1 and not tools_executed_names:
                        self._add_nudge(
                            "[SYSTEM] No tools executed yet. Call an appropriate tool "
                            "before summarizing."
                        )
                        continue
                    try:
                        from core.debug_log import log_completion_exit
                        log_completion_exit(
                            "chat",
                            "planning phase break",
                            step=step,
                            tools_executed=len(tools_executed_names),
                            chat_goals_label=chat_goals.label if chat_goals else "",
                            pending_goals=pending_now,
                            hypothesis_id="D",
                        )
                    except Exception:
                        pass
                    self.ctx_manager.add_message({
                        "role":    "user",
                        "content": "Planning phase over. ACT NOW — use write_file for deliverables.",
                    })
                    break

        try:
            from core.debug_log import log_completion_exit
            log_completion_exit(
                "chat",
                "chat_turn loop finished",
                step=step if "step" in locals() else 0,
                tools_executed=len(tools_executed_names),
                chat_goals_label=chat_goals.label if chat_goals else "",
                hypothesis_id="E",
            )
        except Exception:
            pass

        steps_run = (step + 1) if "step" in locals() else 0
        intent_snapshot = self._active_intent
        self._active_intent = None
        self._anchor_query = ""
        self._chat_goals = None
        self._chat_tools_executed = []
        self._pcap_objective_met = False
        self._pcap_draft = None
        self._mission_goals = None
        self._mission_tools_executed = []
        self._task_plan = None

        content = self._enforce_deliverables_guard(
            paths_written, intent_snapshot, self.workspace_root,
            orig_result=content,
            tools_executed=tools_executed_names,
        )

        if getattr(self, "_last_pcap_summary", None) and "analyze_pcapng" in tools_executed_names:
            if not content.strip() or "Completed this turn" in content:
                warnings_part = ""
                if content.startswith("⚠️"):
                    parts = content.split("\n\n", 1)
                    if len(parts) > 1 and "Completed this turn" in parts[1]:
                        warnings_part = parts[0] + "\n\n"
                content = warnings_part + self._last_pcap_summary

        log_chat_mem(content, steps_run)
        self.ctx_manager.save_state()
        return content

    def _crack_hash_succeeded(self) -> bool:
        for item in getattr(self, "_chat_tool_events", []) or []:
            if isinstance(item, dict) and item.get("name") == "crack_hash" and item.get("success"):
                return True
        return bool(getattr(self, "_crack_results", None))

    def _rehydrate_credential_pairs(self) -> None:
        """Restore hash/salt pairs from session deliverable when resuming after restart."""
        if self._credential_pairs:
            return
        from core.credential_extract import (
            find_xml_salts,
            pair_hashes_with_salts,
            parse_login_hashes,
        )

        candidates: list[Path] = []
        if self._active_intent and self._active_intent.deliverables:
            for rel in self._active_intent.deliverables:
                rel_norm = rel.replace("\\", "/")
                candidates.append(self.workspace_root / "sessions" / self.session_id / Path(rel_norm).name)
                candidates.append(self.workspace_root / rel_norm)
        for path in candidates:
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            hashes = parse_login_hashes(text)
            if not hashes:
                continue
            salts = find_xml_salts(text)
            self._credential_pairs = pair_hashes_with_salts(hashes, salts)
            self._last_pcap_path = self._last_pcap_path or "workspace/last_capture.pcapng"
            # #region agent log
            try:
                from core.debug_log import debug_log
                debug_log(
                    "agent.py:_rehydrate_credential_pairs",
                    "restored pairs from deliverable",
                    {
                        "path": str(path),
                        "hash_count": len(hashes),
                        "has_salt": any(p.get("salt") for p in self._credential_pairs),
                    },
                    "L2",
                )
            except Exception:
                pass
            # #endregion
            return

    async def _bootstrap_pcap_analysis(
        self,
        goals: ChatGoals,
        tools_called: set,
        step_callback=None,
    ) -> list[str]:
        """Deterministic fallback when model stalls on PCAP tasks."""
        executed: list[str] = []
        ff = tools.find_file(goals.pcap_path_hint or "last_capture.pcapng")
        path = ff.get("recommended") or goals.pcap_path_hint or "last_capture.pcapng"

        # #region agent log
        try:
            from core.debug_log import debug_log
            debug_log(
                "agent.py:_bootstrap_pcap_analysis",
                "bootstrap",
                {"path": path, "find_file": ff},
                "F",
            )
        except Exception:
            pass
        # #endregion

        if step_callback:
            step_callback("AGENT_TOOL_CALL", {"tool": "find_file", "args": {"name": goals.pcap_path_hint}})
        did, _ = await self._execute_tool(
            "find_file", {"name": goals.pcap_path_hint or "last_capture.pcapng"}, tools_called, step_callback
        )
        if did:
            executed.append("find_file")

        analyze_args = {
            "file_path": path,
            "filter_expression": goals.filter_expression or "http",
            "limit": 50,
            "verbose": goals.verbose,
        }
        if step_callback:
            step_callback("AGENT_TOOL_CALL", {"tool": "analyze_pcapng", "args": analyze_args})
        did, _ = await self._execute_tool("analyze_pcapng", analyze_args, tools_called, step_callback)
        if did:
            executed.append("analyze_pcapng")

        return executed

    async def _bootstrap_verbose_grep(
        self,
        tools_called: set,
        step_callback=None,
    ) -> list[str]:
        """Deterministic multi-file verbose log search when single grep_file stalls."""
        executed: list[str] = []
        grep_args = {
            "pattern": "xml|Password|Username|616a6178|xmlObj",
            "path_glob": ".pulse/pcap_logs/verbose_*.txt",
            "max_files": 10,
            "case_insensitive": True,
        }
        # #region agent log
        try:
            from core.debug_log import debug_log
            debug_log(
                "agent.py:_bootstrap_verbose_grep",
                "bootstrap find_and_grep",
                grep_args,
                "B",
            )
        except Exception:
            pass
        # #endregion
        if step_callback:
            step_callback("AGENT_TOOL_CALL", {"tool": "find_and_grep", "args": grep_args})
        did, _ = await self._execute_tool("find_and_grep", grep_args, tools_called, step_callback)
        if did:
            executed.append("find_and_grep")
        return executed

    async def _bootstrap_crack_hash(
        self,
        goals: ChatGoals,
        tools_called: set,
        step_callback=None,
    ) -> list[str]:
        """Deterministic crack_hash when model stalls after PCAP extraction."""
        from core.credential_extract import (
            build_cracked_deliverable,
            extract_hash_salt_pairs,
            find_xml_salts,
            pair_hashes_with_salts,
        )

        executed: list[str] = []
        pairs = list(getattr(self, "_credential_pairs", None) or [])
        if not pairs:
            return executed

        if not any(p.get("salt") for p in pairs):
            path = self._last_pcap_path or "workspace/last_capture.pcapng"
            token_args = {
                "file_path": path,
                "filter_expression": 'http.request.uri contains "login_token"',
                "limit": 30,
                "verbose": False,
            }
            # #region agent log
            try:
                from core.debug_log import debug_log
                debug_log(
                    "agent.py:_bootstrap_crack_hash",
                    "supplemental login_token analyze",
                    token_args,
                    "H5",
                )
            except Exception:
                pass
            # #endregion
            did, _ = await self._execute_tool(
                "analyze_pcapng", token_args, tools_called, step_callback
            )
            if did:
                executed.append("analyze_pcapng")
            pairs = list(getattr(self, "_credential_pairs", None) or pairs)
            if not any(p.get("salt") for p in pairs):
                blob = ""
                if self._last_pcap_summary:
                    blob = self._last_pcap_summary
                salts = find_xml_salts(blob)
                if salts:
                    base = [
                        {
                            "hash": p["hash"],
                            "username": p.get("username", ""),
                            "session_token": p.get("session_token", ""),
                        }
                        for p in pairs
                    ]
                    pairs = pair_hashes_with_salts(base, salts)
                    self._credential_pairs = pairs

        mask = str(goals.hints.get("mask") or "NNNNNNAA!")
        for pair in pairs[:5]:
            if not pair.get("hash"):
                continue
            args: dict[str, Any] = {"target_hash": pair["hash"], "mask": mask, "timeout": 180}
            if pair.get("salt"):
                args["salt"] = pair["salt"]
            did, _ = await self._execute_tool("crack_hash", args, tools_called, step_callback)
            if did:
                executed.append("crack_hash")

        if self._crack_results and "write_file" in goals.required_tools:
            wboot = await self._bootstrap_write_cracked(goals, tools_called, step_callback)
            executed.extend(wboot)
        return executed

    async def _bootstrap_write_cracked(
        self,
        goals: ChatGoals,
        tools_called: set,
        step_callback=None,
    ) -> list[str]:
        """Write deliverable with cracked plaintext after crack_hash succeeds."""
        from core.credential_extract import build_cracked_deliverable

        executed: list[str] = []
        if not self._credential_pairs or not self._crack_results:
            return executed
        deliverable = goals.hints.get("deliverable_path", "pwd.txt")
        if self._active_intent and self._active_intent.deliverables:
            deliverable = self._active_intent.deliverables[0]
        content = build_cracked_deliverable(self._credential_pairs, self._crack_results)
        wargs = {"path": deliverable, "content": content}
        # #region agent log
        try:
            from core.debug_log import debug_log
            debug_log(
                "agent.py:_bootstrap_write_cracked",
                "auto-write cracked deliverable",
                {"path": deliverable, "content_len": len(content)},
                "H5",
            )
        except Exception:
            pass
        # #endregion
        did, _ = await self._execute_tool("write_file", wargs, tools_called, step_callback)
        if did:
            executed.append("write_file")
        return executed

    def _enforce_deliverables_guard(
        self,
        paths_written: list[str],
        intent_snapshot: TaskIntent,
        workspace_root: Path,
        orig_result: str,
        tools_executed: list[str] = None,
    ) -> str:
        """Verify deliverables on disk; warn on hallucinated completion."""
        warnings: list[str] = []
        content = orig_result

        normalized_written = [p.replace("\\", "/") for p in paths_written]

        if intent_snapshot and intent_snapshot.deliverables:
            for rel in intent_snapshot.deliverables:
                rel_norm = rel.replace("\\", "/")
                p = workspace_root / rel_norm if not Path(rel_norm).is_absolute() else Path(rel_norm)
                if p.exists():
                    continue
                if any(
                    w == rel_norm or w.endswith("/" + rel_norm) or w.endswith(rel_norm)
                    for w in normalized_written
                ):
                    continue
                warnings.append(f"Deliverable not found on disk: {rel_norm}")

        if intent_snapshot and intent_snapshot.is_dev_task:
            deliverable_written = any(
                Path(p.replace("\\", "/")).suffix in (".py", ".ps1")
                for p in paths_written
            )
            if not deliverable_written and intent_snapshot.deliverables:
                if re.search(
                    r"\b(saved|written|created|verified|mission complete|has been saved)\b",
                    content, re.I,
                ):
                    warnings.append(
                        "No code deliverable was written — only workspace notes may have been updated."
                    )

        if re.search(r'\{"name"\s*:', content.strip()):
            summary = ReActAgent._format_tool_summary(tools_executed or self._chat_tools_executed or [], paths_written)
            content = summary if not content.strip().startswith("⚠️") else content

        if warnings:
            prefix = "⚠️ " + " | ".join(warnings) + "\n\n"
            if not content.startswith("⚠️"):
                content = prefix + content

        return content

    @staticmethod
    def _build_script_failure_hint(
        tool_name: str,
        result: Any,
        tool_args: dict,
    ) -> str | None:
        """Dialectical recovery hint after run_script import/runtime failures."""
        if tool_name != "run_script" or not isinstance(result, dict):
            return None
        if result.get("exit_code", 0) == 0:
            return None

        missing = result.get("missing_module")
        pip_cmd = result.get("pip_install_command")
        script = result.get("script") or tool_args.get("script_path", "script")
        cwd = result.get("cwd", "")

        if missing and pip_cmd:
            return (
                "[SCRIPT FAILURE — HYPOTHESIS / ANTITHESIS / SYNTHESIS]\n"
                f"HYPOTHESIS: '{script}' failed because Python module '{missing}' is missing in interpreter "
                f"{result.get('interpreter', 'unknown')}.\n"
                f"ANTITHESIS: Do NOT claim completion via append_note; do NOT retry identical run_script args.\n"
                f"SYNTHESIS:\n"
                f"1) host_exec: {pip_cmd}\n"
                f"2) run_script: same script_path='{tool_args.get('script_path', script)}' cwd='{cwd}'\n"
                f"3) If still failing, read_file the script and inspect imports before next action."
            )

        stderr = str(result.get("stderr", "") or result.get("error", ""))[:400]
        if stderr:
            return (
                "[SCRIPT FAILURE — RECOVER]\n"
                f"Script '{script}' failed. stderr: {stderr}\n"
                "Read the script, fix environment or arguments, then re-run run_script."
            )
        return None

    @staticmethod
    def _build_grep_miss_hint(tool_name: str, result: Any, args: dict | None) -> str | None:
        """After a zero-match grep on verbose logs, steer toward multi-file / broader patterns."""
        if tool_name != "grep_file" or not isinstance(result, dict) or result.get("success") is False:
            return None
        if result.get("match_count", 0) > 0:
            return None
        path = str((args or {}).get("path", "")).replace("\\", "/").lower()
        pattern = str((args or {}).get("pattern", ""))
        if "verbose" not in path and "pcap_logs" not in path:
            return None
        # #region agent log
        try:
            from core.debug_log import debug_log
            debug_log(
                "agent.py:_build_grep_miss_hint",
                "zero-match verbose grep",
                {"path": path, "pattern": pattern, "case_insensitive": (args or {}).get("case_insensitive")},
                "B",
            )
        except Exception:
            pass
        # #endregion
        return (
            "[SYSTEM] grep_file returned 0 matches on a verbose log. "
            "Credentials are often hex-encoded — 'xmlObj|password' may not appear literally.\n"
            "Next: find_and_grep(pattern='xml|Password|Username|616a6178|xmlObj', "
            "path_glob='.pulse/pcap_logs/verbose_*.txt', case_insensitive=true, max_files=10)\n"
            "Or: analyze_pcapng with filter_expression='xml' (not only 'http')."
        )

    @staticmethod
    def _build_failure_playbook_hint(tool_name: str, result: Any) -> str | None:
        """Inject a corrective playbook excerpt after host_exec/run_script failures."""
        if tool_name not in ("host_exec", "run_script") or not isinstance(result, dict):
            return None
        failed = (
            result.get("success") is False
            or result.get("exit_code", 0) not in (0, None)
        )
        if not failed:
            return None
        stderr = str(result.get("stderr", "") or result.get("error", ""))
        patterns = (
            r"ModuleNotFoundError",
            r"No module named",
            r"extensi.*\.ps1",
            r"\.py'",
            r"not found",
        )
        if not any(re.search(p, stderr, re.I) for p in patterns):
            return None
        try:
            from core.rag import get_rag_context_for_tools
            excerpt = get_rag_context_for_tools(["run_script", "host_exec"], stderr, max_chars=600)
        except Exception:
            return None
        if not excerpt:
            return None
        return f"[SYSTEM] Tool failure — follow this playbook excerpt:\n{excerpt}"

    @staticmethod
    def _build_tool_reflection_hint(tool_name: str, result: Any) -> str | None:
        """Inject the tool's schema + playbook excerpt to help self-correct after failure."""
        if not isinstance(result, dict) or result.get("success") is not False:
            return None

        # Avoid double hinting if we already have a host_exec/run_script failure hint
        if tool_name in ("host_exec", "run_script"):
            stderr = str(result.get("stderr", "") or result.get("error", ""))
            patterns = (
                r"ModuleNotFoundError",
                r"No module named",
                r"extensi.*\.ps1",
                r"\.py'",
                r"not found",
            )
            if any(re.search(p, stderr, re.I) for p in patterns):
                return None

        # Find schema
        schema = next((s for s in tools.TOOLS_SCHEMA if s.get("function", {}).get("name") == tool_name), None)
        schema_str = ""
        if schema:
            schema_str = f"Tool Schema:\n{json.dumps(schema.get('function', {}), indent=2)}\n"

        # Find playbook from RAG
        playbook = ""
        try:
            from core.rag import get_rag_context_for_tools
            playbook = get_rag_context_for_tools([tool_name], max_chars=1200)
        except Exception:
            pass

        hint_parts = [
            f"[SYSTEM] Tool '{tool_name}' failed.",
        ]
        if schema_str:
            hint_parts.append(schema_str)
        if playbook:
            hint_parts.append(f"Playbook Excerpt:\n{playbook}")

        # Dynamic Troubleshooting Playbooks
        error_msg = str(result.get("error", "")).lower()
        if "does not exist" in error_msg or "not found" in error_msg or "no such file" in error_msg:
            hint_parts.append(
                "TROUBLESHOOTING:\n"
                "- The file was not found. Do NOT blindly retry with the same path.\n"
                "- Use `find_file` to locate it, or `host_exec` with Get-ChildItem on the expected parent directory.\n"
                "- If you generated a script that wrote this file, use `read_file` on the script to verify the working directory it used."
            )
        elif "not in registry" in error_msg or "not found" in error_msg and tool_name not in [s.get("function", {}).get("name") for s in tools.TOOLS_SCHEMA]:
            hint_parts.append(
                "TROUBLESHOOTING:\n"
                f"- The tool '{tool_name}' does not exist. Do NOT try to call it again.\n"
                "- Use `host_exec` for shell commands (like `mv`, `cp`, `mkdir`) if a specialized tool doesn't exist, or find an alternative tool."
            )
        elif tool_name == "read_file" and ("is a directory" in error_msg or "is not a file" in error_msg):
            hint_parts.append(
                "TROUBLESHOOTING:\n"
                "- You tried to read a directory as a file.\n"
                "- Use `host_exec` with Get-ChildItem or `find_file` to inspect directory contents."
            )
        elif tool_name == "run_script":
            hint_parts.append(
                "TROUBLESHOOTING:\n"
                "- The script execution failed. Ensure the script has the correct extension (e.g. `.py` or `.ps1`).\n"
                "- If the script threw an error, use `sequentialthinking` to analyze the traceback before retrying."
            )

        hint_parts.append(
            f"Please review the schema and use an investigative tool (find_file, read_file, host_exec) to troubleshoot before calling '{tool_name}' again."
        )
        return "\n\n".join(hint_parts)

    @staticmethod
    def _format_tool_summary(tools_executed: list[str], paths_written: list[str]) -> str:
        lines = ["Completed this turn:"]
        for name in dict.fromkeys(tools_executed):
            lines.append(f"  - {name}")
        if paths_written:
            lines.append("Files written:")
            for p in dict.fromkeys(paths_written):
                lines.append(f"  - {p}")
        return "\n".join(lines)

    # ── Final synthesis ────────────────────────────────────────────────────

    async def _final_synthesis(self) -> str:
        """
        Generate a structured final report grounded in the findings database.
        Format: ### Summary | ### Technical Findings | ### Next Steps
        """
        # Grounding: pull findings count from DB
        grounding = ""
        try:
            f_data = tools.finding_list().get("findings", [])
            grounding = (
                f"\n[FINDINGS INVENTORY]\n"
                f"Total findings in DB: {len(f_data)}\n"
                + "\n".join(
                    f"  [{f.get('severity')}] {f.get('title')} — {f.get('target', 'n/a')}"
                    for f in f_data[:20]
                )
            )
        except Exception:
            pass

        synthesis_prompt = (
            f"MISSION COMPLETE — generate the final engagement report.\n"
            f"{grounding}\n\n"
            "Structure your report as:\n"
            "### Summary\n"
            "### Technical Findings\n"
            "### Next Steps"
        )
        self.ctx_manager.add_message({"role": "user", "content": synthesis_prompt})

        try:
            response = await self.adapter.chat(
                messages=self.ctx_manager.get_messages(),
                tools_schema=None,
                options={
                    "temperature": 0.2,
                    "num_predict": self.num_predict_synthesis,
                },
                anchor_query=self._anchor_query,
            )
            final = response.get("message", {}).get("content", "Mission complete.")
        except Exception:
            final = "Mission complete. Unable to generate synthesis."

        if re.search(r'\{\s*"name"\s*:', (final or "").strip()):
            report = tools.report_generate()
            if isinstance(report, dict) and report.get("success"):
                path = report.get("report_path", "")
                final = (
                    "### Summary\n"
                    "Mission complete. Report generated from recorded findings.\n\n"
                    "### Technical Findings\n"
                    f"- Report path: {path}\n"
                    f"- Findings count: {report.get('findings_count', 0)}\n\n"
                    "### Next Steps\n"
                    "- Review the generated markdown report and validate remediations."
                )
            else:
                summary = self._format_tool_summary(
                    getattr(self, "_mission_tools_executed", []) or getattr(self, "_chat_tools_executed", []),
                    [],
                )
                final = (
                    "### Summary\nMission complete.\n\n"
                    "### Technical Findings\n"
                    f"{summary}\n\n"
                    "### Next Steps\nReview the above tool outputs for details."
                )

        if self._mission_tracker and self._mission_tracker.retrieval_mission and not self._mission_tracker.objective_satisfied():
            if not final.startswith("⚠️"):
                final = (
                    "⚠️ Retrieval objective may be incomplete (no confirmed credential evidence).\n\n"
                    + final
                )

        # #region agent log
        try:
            from core.debug_log import debug_log
            _stripped = (final or "").strip()
            debug_log(
                "agent.py:_final_synthesis",
                "synthesis output",
                {
                    "final_len": len(final or ""),
                    "final_head": _stripped[:300],
                    "looks_like_toolcall": bool(re.search(r'\{\s*"name"\s*:', _stripped)),
                },
                "E", "run1",
            )
        except Exception:
            pass
        # #endregion

        self.ctx_manager.add_message({"role": "assistant", "content": final})
        return final
