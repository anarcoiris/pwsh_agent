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
from core.llm_utils import (
    ArgumentNormalizer,
    DynamicContextBuilder,
    ResultCompactor,
    RetryOrchestrator,
    SequentialThinkingEngine,
)
from core.parser import AgentOutputParser
from core.task_intent import TaskIntent, TaskIntentExtractor
from core.chat_goals import ChatGoals, ChatGoalGuard
from core.write_guard import WriteGuard
from core.execution_policy import ExecutionPolicy

logger = logging.getLogger("pwsh_agent.agent")

_PROJECT_ROOT = Path(__file__).resolve().parent


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

    def __init__(self, host: str, model: str, parser: AgentOutputParser):
        self.host   = host
        self.model  = model
        self.parser = parser
        self.client = AsyncClient(host=host, timeout=httpx.Timeout(300.0))

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools_schema: list[dict[str, Any]] | None = None,
        options: dict | None = None,
        max_retries: int = 3,
        task_intent: TaskIntent | None = None,
    ) -> dict[str, Any]:
        try:
            for injection in ContextRouter.build_injections(messages, task_intent):
                messages = messages + [injection]
        except Exception as e:
            logger.warning("ContextRouter injection error: %s", e)

        _options = {"temperature": 0.3, "num_ctx": 16384, "num_predict": 4096}
        if options:
            _options.update(options)

        user_msgs = [m for m in messages if m.get("role") == "user"]
        latest_user = user_msgs[-1].get("content", "") if user_msgs else ""
        self.parser.set_user_context(latest_user)

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

    MIN_TOOLS_BEFORE_COMPLETE: int = 2

    def __init__(self, session_id: str = "default"):
        self.project_root = _PROJECT_ROOT
        self.config       = self._load_config()

        ollama_cfg = self.config.get("ollama", {})
        self.base_url      = ollama_cfg.get("base_url", "http://localhost:11435")
        self.default_model = ollama_cfg.get("default_model", "qwen2.5-coder:7b")

        agent_cfg        = self.config.get("agent", {})
        self.max_steps   = agent_cfg.get("max_steps", 15)
        self.max_thoughts = agent_cfg.get("max_thoughts", 15)

        # ── Specialist & safety state ──────────────────────────────────────
        self.active_specialist: str = "lead"
        self.network_mode: str      = "SANDBOX"

        # ── Core engines ───────────────────────────────────────────────────
        self.thinking_engine    = SequentialThinkingEngine(max_thoughts=self.max_thoughts)
        self.retry_orchestrator = RetryOrchestrator()

        # Tools registry
        self.tools_registry: dict[str, Any] = {
            # Cognitive
            "sequentialthinking":     self.thinking_engine.process_thought,
            # System
            "host_exec":              tools.host_exec,
            "run_script":             tools.run_script,
            "find_file":              tools.find_file,
            "read_file":              tools.read_file,
            "write_file":             tools.write_file,
            "append_note":            tools.append_note,
            # Network capture
            "list_network_interfaces": tools.list_network_interfaces,
            "capture_packets":        tools.capture_packets,
            "analyze_pcapng":         tools.analyze_pcapng,
            # Hash cracking
            "crack_hash":             tools.crack_hash,
            # Recon
            "dns_lookup":             tools.dns_lookup,
            "ping_sweep":             tools.ping_sweep,
            "port_scan":              tools.port_scan,
            "http_headers_check":     tools.http_headers_check,
            "ssl_analysis":           tools.ssl_analysis,
            "cve_lookup":             tools.cve_lookup,
            "system_info":            tools.system_info,
            # Intel
            "encode_decode":          tools.encode_decode,
            "hash_identify":          tools.hash_identify,
            # Findings
            "finding_create":         tools.finding_create,
            "finding_list":           tools.finding_list,
            "report_generate":        tools.report_generate,
        }

        self.parser  = AgentOutputParser(self.tools_registry)
        self.adapter = OllamaAdapter(
            host=self.base_url,
            model=self.default_model,
            parser=self.parser,
        )

        # Session persistence
        self.session_id  = session_id
        self.ctx_manager = AgentContextManager(
            mode="autonomous",
            session_id=session_id,
            max_total_context=100,
        )

        # Cancellation
        self._cancel_event = asyncio.Event()
        self._active_intent: TaskIntent | None = None

        # Initialise system prompt if fresh session
        if not self.ctx_manager.has_system():
            self._init_system_prompt()

    # ── Configuration ──────────────────────────────────────────────────────

    def _load_config(self) -> dict:
        config_path = self.project_root / "config.yaml"
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

        identity_context = ""
        for fn in ("SOUL.md", "IDENTITY.md", "USER.md"):
            fp = self.project_root / fn
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
            "1. Always start with a sequentialthinking call to plan your approach.\n"
            "2. For multi-step MISSIONS: use append_note on workspace/plan.md and workspace/status.md for progress (never overwrite with write_file). For user-requested deliverables (.py, .ps1, .md reports), use write_file to the exact path the user named.\n"
            "3. Execute tools one at a time; inspect each result before proceeding.\n"
            "4. Keep a persistent checklist updated via append_note on workspace/status.md.\n"
            "5. Register significant discoveries with finding_create.\n"
            "6. When finished (mission mode only), declare MISSION_COMPLETE and summarise findings.\n\n"

            "DELIVERABLE RULES:\n"
            "- User-requested files (e.g. watcher/watcher.py) MUST be written with write_file to that path.\n"
            "- workspace/plan.md and workspace/status.md are progress logs ONLY — use append_note, never write_file for short status lines.\n"
            "- Do NOT substitute plan.md for a code deliverable. Do NOT claim a script was created until write_file succeeded on the target path.\n\n"

            "TOOL CALL FORMAT — prefer native tool_calls, or use XML tags:\n"
            "<tool_call>\n"
            '{"name": "host_exec", "arguments": {"command": "Get-Process"}}\n'
            "</tool_call>\n\n"

            "AVAILABLE TOOLS:\n"
            "— Core — sequentialthinking, host_exec, run_script, find_file, read_file, write_file, append_note\n"
            "— Network Capture — list_network_interfaces, capture_packets, analyze_pcapng\n"
            "— Recon — dns_lookup, ping_sweep, port_scan, http_headers_check, ssl_analysis, cve_lookup, system_info\n"
            "— Intel — encode_decode, hash_identify, crack_hash\n"
            "— Findings — finding_create, finding_list, report_generate\n\n"

            "CRITICAL RULES:\n"
            "- Progress notes: append_note on workspace/plan.md or workspace/status.md. Code deliverables: write_file to the user-specified path.\n"
            "- Do NOT invent facts. Run a tool to verify everything.\n"
            "- Emit only ONE tool call per turn.\n"
            "- Never repeat the exact same tool call with identical arguments.\n"
            "- Use finding_create immediately when you discover a significant issue.\n"
            "- Executing direct shell commands via host_exec is a strict LAST RESORT. Always prefer high-level specialized tools (e.g. port_scan, dns_lookup, system_info, analyze_pcapng) whenever they are available to minimize raw shell usage.\n"
        )

        self.ctx_manager.clear_history()
        self.ctx_manager.add_message({"role": "system", "content": prompt})

    # ── Public interface ───────────────────────────────────────────────────

    def clear_history(self):
        """Reset memory, thinking engine, and retry state."""
        self.thinking_engine.reset()
        self.retry_orchestrator.reset()
        self._init_system_prompt()

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

        tool_name, tool_args, redirect_note = ExecutionPolicy.apply(tool_name, tool_args)

        pending = (
            self._active_intent.pending_deliverables(self.project_root)
            if self._active_intent else []
        )
        tool_name, tool_args, block_err = WriteGuard.apply(
            tool_name,
            tool_args,
            self._active_intent,
            session_id=self.session_id,
            pending_deliverables=pending,
        )
        if not block_err:
            executed_so_far = getattr(self, "_chat_tools_executed", [])
            tool_name, tool_args, block_err = ChatGoalGuard.apply(
                tool_name, tool_args,
                getattr(self, "_chat_goals", None),
                executed_so_far,
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

        failure_hint = self._build_failure_playbook_hint(tool_name, result)
        if failure_hint:
            self.ctx_manager.add_message({
                "role":    "user",
                "content": failure_hint,
            })

        duration_ms = int((time.monotonic() - t_start) * 1000)
        result_str  = json.dumps(result, default=str)
        result_hash = hashlib.sha256(result_str.encode()).hexdigest()

        # Compact large results
        result_str = ResultCompactor.compact(tool_name, result_str)

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
            parts = []
            for key in ("packet_summary", "potential_plaintext_credentials", "protocol_hierarchy"):
                val = analysis.get(key)
                if val:
                    parts.append(f"### {key}\n{val[:1500]}")
            if parts:
                self._last_pcap_summary = "\n\n".join(parts)

        self.ctx_manager.add_message({
            "role":    "tool",
            "name":    tool_name,
            "content": result_str,
        })

        return True, 1

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
        self._active_intent = TaskIntentExtractor.parse(user_prompt)
        self.ctx_manager.add_message({"role": "user", "content": user_prompt})

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

            # MISSION_COMPLETE guard
            if "MISSION_COMPLETE" in content:
                if tools_executed >= self.MIN_TOOLS_BEFORE_COMPLETE:
                    final_answer = await self._final_synthesis()
                    if step_callback:
                        step_callback("MISSION_COMPLETED", final_answer)
                    break
                else:
                    # Not enough tools run — reject and keep going
                    self.ctx_manager.add_message({
                        "role":    "user",
                        "content": (
                            f"[SYSTEM] MISSION_COMPLETE rejected — only {tools_executed} "
                            f"tool(s) executed (minimum: {self.MIN_TOOLS_BEFORE_COMPLETE}). "
                            "Continue your investigation."
                        ),
                    })
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
            else:
                # No tool call — attempt parser reflection
                consecutive_empty += 1

                reflection = self.retry_orchestrator.parser_reflection(content, self.parser)

                if reflection:
                    logger.info("Parser reflection triggered (step %d)", step)
                    if step_callback:
                        step_callback("AGENT_THOUGHT", "[Parser reflection — self-correcting…]")
                    await self._execute_tool(
                        reflection["function"]["name"],
                        reflection["function"]["arguments"],
                        tools_called,
                        step_callback,
                    )
                else:
                    # Hard stall nudge
                    nudge = (
                        "[SYSTEM DIRECTIVE] You are stalling. "
                        "Execute a technical tool NOW — use sequentialthinking to plan, "
                        "then immediately call a recon or execution tool. "
                        "Do NOT produce prose."
                    )
                    self.ctx_manager.add_message({"role": "user", "content": nudge})

                # If still conversational after a few empty turns, return early
                if consecutive_empty >= 3 and step == 0:
                    final_answer = content
                    break

        # Hit step limit — synthesise
        if not final_answer:
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

        # Detect confirmation phrases and add execution directive
        if any(w in message.lower() for w in
               ["yes", "ok", "do it", "go ahead", "execute", "proceed", "run it"]):
            message += "\n\n[SYSTEM DIRECTIVE: User confirmed. Execute the tool NOW. No prose.]"

        self.retry_orchestrator.reset()
        self.parser.set_user_context(message)
        self._active_intent = TaskIntentExtractor.parse(message)

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
            "Use append_note for workspace/plan.md progress — never write_file for status lines. "
            "sequentialthinking is optional in chat (max one planning thought); prefer action tools. "
            "Complete the user's task before stopping — append_note alone is not completion. "
            f"{deliverable_hint}\n"
        )
        self.ctx_manager.add_message({"role": "user", "content": chat_directive + message})

        chat_goals = ChatGoals.from_message(message)
        if not chat_goals:
            chat_goals = ChatGoals.from_session(
                self.ctx_manager.get_messages(), message
            )
        self._chat_goals = chat_goals
        self._last_pcap_summary: str | None = None

        if chat_goals and chat_goals.context_directive():
            self.ctx_manager.add_message({
                "role": "user",
                "content": chat_goals.context_directive(),
            })

        goal_nudges = 0
        max_goal_nudges = 4

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
        self._chat_tools_executed: list[str] = []

        for step in range(10):
            self._chat_tools_executed = list(tools_executed_names)
            self.ctx_manager.trim_context()
            response = await self.adapter.chat(
                messages=self.ctx_manager.get_messages(),
                tools_schema=tools.TOOLS_SCHEMA,
                task_intent=self._active_intent,
            )

            msg     = response.get("message", {})
            content = msg.get("content", "") or ""
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
                    did_exec, _ = await self._execute_tool(name, args, tools_called, step_callback)
                    if did_exec:
                        tools_executed_names.append(name)
                        if name == "write_file" and args.get("path"):
                            paths_written.append(str(args["path"]).replace("\\", "/"))

                self._chat_tools_executed = list(tools_executed_names)

                if chat_goals and not chat_goals.pending(tools_executed_names):
                    break

                pending = self._active_intent.pending_deliverables(self.project_root)
                if pending and deliverable_nudges < 2:
                    deliverable_nudges += 1
                    self.ctx_manager.add_message({
                        "role":    "user",
                        "content": (
                            f"[SYSTEM] Deliverable not on disk yet: {pending[0]}. "
                            "Call write_file NOW with the full script content."
                        ),
                    })
            else:
                salvage = self.parser.salvage_tool_call(content, user_context=message)
                pending_goals = chat_goals.pending(tools_executed_names) if chat_goals else []

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
                            pending_goals = chat_goals.pending(tools_executed_names)
                    if chat_goals.pending(tools_executed_names):
                        self.ctx_manager.add_message({
                            "role": "user",
                            "content": chat_goals.nudge_text(
                                chat_goals.pending(tools_executed_names)
                            ),
                        })
                        continue

                if content and ("?" in content or "¿" in content):
                    log_chat_mem(content, step + 1)
                    self.ctx_manager.save_state()
                    intent_snapshot = self._active_intent
                    self._active_intent = None
                    return self._finalize_chat_response(
                        message, content, tools_executed_names,
                        paths_written, intent_snapshot, self.project_root,
                    )
                if step > 0 and not (chat_goals and chat_goals.pending(tools_executed_names)):
                    break
                reflection = self.retry_orchestrator.parser_reflection(content, self.parser)
                if reflection:
                    rname = reflection["function"]["name"]
                    rargs = reflection["function"]["arguments"]
                    did_exec, _ = await self._execute_tool(
                        rname, rargs, tools_called, step_callback
                    )
                    if did_exec and rname != "sequentialthinking":
                        tools_executed_names.append(rname)
                        if rname == "write_file" and rargs.get("path"):
                            paths_written.append(str(rargs["path"]).replace("\\", "/"))
                else:
                    self.ctx_manager.add_message({
                        "role":    "user",
                        "content": "Planning phase over. ACT NOW — use write_file for deliverables.",
                    })

        steps_run = (step + 1) if "step" in locals() else 0
        intent_snapshot = self._active_intent
        self._active_intent = None
        self._chat_goals = None
        self._chat_tools_executed = []

        content = self._finalize_chat_response(
            message, content, tools_executed_names,
            paths_written, intent_snapshot, self.project_root,
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

    @staticmethod
    def _finalize_chat_response(
        user_message: str,
        content: str,
        tools_executed: list[str],
        paths_written: list[str],
        intent: TaskIntent | None,
        project_root: Path,
    ) -> str:
        """Verify deliverables on disk; warn on hallucinated completion."""
        warnings: list[str] = []

        normalized_written = [p.replace("\\", "/") for p in paths_written]

        if intent and intent.deliverables:
            for rel in intent.deliverables:
                rel_norm = rel.replace("\\", "/")
                p = project_root / rel_norm if not Path(rel_norm).is_absolute() else Path(rel_norm)
                if p.exists():
                    continue
                if any(
                    w == rel_norm or w.endswith("/" + rel_norm) or w.endswith(rel_norm)
                    for w in normalized_written
                ):
                    continue
                warnings.append(f"Deliverable not found on disk: {rel_norm}")

        if intent and intent.is_dev_task:
            deliverable_written = any(
                Path(p.replace("\\", "/")).suffix in (".py", ".ps1")
                for p in paths_written
            )
            if not deliverable_written and intent.deliverables:
                if re.search(
                    r"\b(saved|written|created|verified|mission complete|has been saved)\b",
                    content, re.I,
                ):
                    warnings.append(
                        "No code deliverable was written — only workspace notes may have been updated."
                    )

        if re.search(r'\{"name"\s*:', content.strip()):
            summary = ReActAgent._format_tool_summary(tools_executed, paths_written)
            content = summary if not content.strip().startswith("⚠️") else content

        if warnings:
            prefix = "⚠️ " + " | ".join(warnings) + "\n\n"
            if not content.startswith("⚠️"):
                content = prefix + content

        return content

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
                messages=self.ctx_manager.get_messages()
            )
            final = response.get("message", {}).get("content", "Mission complete.")
        except Exception:
            final = "Mission complete. Unable to generate synthesis."

        self.ctx_manager.add_message({"role": "assistant", "content": final})
        return final
