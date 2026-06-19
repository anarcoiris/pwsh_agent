"""Map malformed LLM tool-call output to valid tool calls — FORMAT HARNESS ONLY.

This module does NOT infer intent from user context. It only:
  1. Detects prose stalls (model emitting status text instead of acting).
  2. Catches tool calls that are structurally close to valid but malformed
     (e.g. tool_name {args} without proper wrapping, Python-style calls).
  3. Redirects tool calls that are misrouted by format (not by intent).
  4. When the harness finds nothing, returns None — the caller should ask
     VibeThinker to reformulate the instruction to the executor model.

Domain-specific salvage logic (pcap/hash/credentials) has been removed.
VibeThinker handles strategy when the executor model fails to produce valid output.
"""

from __future__ import annotations

import json
import re
from typing import Any

# ──────────────────────────────────────────────────────────────────────────────
# Format-level detectors  (kept: these are format signals, not intent signals)
# ──────────────────────────────────────────────────────────────────────────────

# Model emits status prose instead of tool calls — treat as stall, not completion.
PROSE_STALL_RE = re.compile(
    r"\[SYSTEM\]\s*Task complete|\[STATUS\]\s*MISSION_|\*\*Next Steps:\*\*|\*\*Final Thoughts:\*\*",
    re.I | re.M,
)

# Model echoes the CURRENT STATE handoff banner instead of acting.
_HANDOFF_ECHO_RE = re.compile(
    r"\[HANDOFF COMPLETE[^\]]*\]",
    re.I,
)

# PCAP/log content search glob patterns (kept for redirect_misrouted_search_tool)
_PCAP_LOG_GLOB_RE = re.compile(r"verbose_[\w.*-]*\.txt|\.pulse/", re.I)

# Python-style call: tool_name(arg=value, ...)
_PYTHON_CALL_RE = re.compile(
    r'\b([\w]+)\s*\(\s*((?:["\']?\w+["\']?\s*=\s*[^,)]+,?\s*)+)\)',
    re.DOTALL,
)

# Bare call: tool_name {"key": "value"} (no wrapper)
# Built dynamically against the registered tools list.


def _default_registered_tools() -> frozenset[str]:
    """Best-effort list of known tools (used when registry not available)."""
    return frozenset({
        "append_note", "find_file", "analyze_pcapng", "host_exec", "run_script",
        "read_file", "write_file", "sequentialthinking", "capture_packets",
        "list_network_interfaces", "crack_hash", "system_info", "port_scan",
        "ping_sweep", "dns_lookup", "find_and_grep", "grep_file",
        "http_get", "http_headers_check", "ssl_analysis", "try_http_login",
        "encode_decode", "hash_identify", "delegate_to", "cve_lookup",
        "finding_create", "finding_list", "report_generate",
    })


_DEFAULT_REGISTERED_TOOLS = _default_registered_tools()


def _try_parse_json(s: str) -> Any | None:
    """Parse JSON with escape-fix fallback."""
    s = s.strip()
    if not s:
        return None
    for candidate in (s, re.sub(r'\\([^nrt\\"\/ubf])', r'\\\\\1', s)):
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Format harness: detect malformed tool calls
# ──────────────────────────────────────────────────────────────────────────────

def salvage_intent_tool_call(
    raw_content: str,
    user_context: str = "",
    *,
    session_id: str | None = None,
    registered_tools: frozenset[str] | None = None,
) -> dict[str, Any] | None:
    """Harness-only salvage: detect structurally malformed tool calls.

    Checks if the model's output contains registered tool names followed by
    JSON-like argument structures that are close to valid but not wrapped
    correctly. Does NOT infer intent from context.

    Returns a normalised {function: {name, arguments}} dict, or None.
    When None is returned, the caller should escalate to VibeThinker.
    """
    if not raw_content:
        return None

    tools = registered_tools or _DEFAULT_REGISTERED_TOOLS
    content = raw_content.strip()

    # 1. Handoff echo — model is narrating instead of acting
    if _HANDOFF_ECHO_RE.search(raw_content):
        return _handoff_return_call(user_context, session_id=session_id)

    # 2. Python-style call: tool_name(key=value, ...)
    for m in _PYTHON_CALL_RE.finditer(content):
        name = m.group(1)
        if name not in tools:
            continue
        # Try to convert Python kwargs to a JSON dict
        kwargs_str = m.group(2)
        args: dict[str, Any] = {}
        for kv in re.finditer(r'(\w+)\s*=\s*(["\']?)([^,)]+)\2', kwargs_str):
            key = kv.group(1)
            val_raw = kv.group(3).strip()
            # Coerce obvious types
            if val_raw.lower() in ("true", "false"):
                args[key] = val_raw.lower() == "true"
            elif val_raw.isdigit():
                args[key] = int(val_raw)
            else:
                args[key] = val_raw.strip('"\'')
        if args:
            return {"function": {"name": name, "arguments": args}}

    # 3. Bare JSON object after tool name: tool_name {"key": "value"}
    for tool_name in tools:
        pattern = rf'\b{re.escape(tool_name)}\s+(\{{.*?\}})'
        m = re.search(pattern, content, re.DOTALL)
        if m:
            parsed = _try_parse_json(m.group(1))
            if parsed and isinstance(parsed, dict):
                return {"function": {"name": tool_name, "arguments": parsed}}

    # 4. Tool name on its own line followed immediately by a JSON block
    for tool_name in tools:
        pattern = rf'(?:^|\n)\s*{re.escape(tool_name)}\s*\n\s*(\{{.*?\}})'
        m = re.search(pattern, content, re.DOTALL | re.MULTILINE)
        if m:
            parsed = _try_parse_json(m.group(1))
            if parsed and isinstance(parsed, dict):
                return {"function": {"name": tool_name, "arguments": parsed}}

    return None


def looks_like_prose_stall(content: str) -> bool:
    """True when the model emitted status prose instead of a tool call."""
    text = (content or "").strip()
    if not text:
        return False
    if PROSE_STALL_RE.search(text):
        return True
    # Reasoning-only turn: long prose, no tool_call/json fence
    if len(text) > 80 and not re.search(r"<tool_call>|```(?:json)?\s*\{", text, re.I):
        if re.search(r"\b(will use|next step|filter the content|analyze the filtered)\b", text, re.I):
            return True
    return False


def redirect_misrouted_search_tool(
    tool_name: str,
    args: dict[str, Any],
    anchor_query: str,
) -> tuple[str, dict[str, Any], str | None]:
    """Block find_and_grep/grep_file aimed at .pulse logs when looking for files by name.

    Format-level redirect: the model called the wrong tool class.
    Returns (tool_name, args, redirect_note_or_none).
    """
    from core.task_intent import extract_filename_globs, is_file_discovery_mission

    if not anchor_query or not is_file_discovery_mission(anchor_query):
        return tool_name, dict(args or {}), None

    globs = extract_filename_globs(anchor_query)
    if not globs:
        return tool_name, dict(args or {}), None

    path_key = "path_glob" if tool_name == "find_and_grep" else "path"
    path = str((args or {}).get(path_key) or (args or {}).get("path") or "")
    path_lower = path.replace("\\", "/").lower()

    misrouted = (
        tool_name == "find_and_grep"
        and (not path or ".pulse" in path_lower or "verbose_" in path_lower)
    ) or (
        tool_name == "grep_file"
        and (_PCAP_LOG_GLOB_RE.search(path) or ".pulse" in path_lower)
    )
    if not misrouted:
        return tool_name, dict(args or {}), None

    note = (
        f"Redirected: locate files by name with find_file('{globs[0]}'), "
        "then read_file(path=<recommended>). find_and_grep searches inside file contents, "
        "not filenames under workspace."
    )
    return "find_file", {"name": globs[0]}, note


def mission_lead_dev_guard(
    tool_name: str,
    tools_executed: list[str],
    anchor_query: str,
    *,
    active_agent: str = "lead",
    tool_args: dict | None = None,
    last_delegate_brief: str = "",
) -> str | None:
    """Block LEAD planning loops on dev/script missions — force delegate_to(workspace)."""
    if active_agent != "lead":
        return None
    from core.task_intent import detect_mission_kind

    if detect_mission_kind(anchor_query) != "hygiene_remediation":
        return None

    done = list(tools_executed or [])
    has_delegate = "delegate_to" in done
    write_count = done.count("write_file")
    delegate_count = done.count("delegate_to")

    if tool_name in ("run_script", "host_exec", "write_file", "read_file", "grep_file"):
        return (
            f"Blocked: {tool_name} is workspace-owned. "
            "Call delegate_to(agent='workspace', brief=<next script task>) — LEAD orchestrates only."
        )

    if tool_name == "sequentialthinking" and done.count("sequentialthinking") >= 1:
        return (
            "Blocked: one planning thought is enough. "
            "Call delegate_to(agent='workspace', brief=<script task>) now — "
            "do NOT repeat sequentialthinking."
        )
    if (
        tool_name == "append_note"
        and done.count("append_note") >= 2
        and write_count < delegate_count
    ):
        return (
            "Blocked: enough status notes. "
            "Wait for workspace write_file or delegate the next script."
        )
    if tool_name == "delegate_to":
        brief = str((tool_args or {}).get("brief", "")).strip()
        if brief and brief == last_delegate_brief.strip() and write_count >= delegate_count:
            nxt = write_count + 1
            return (
                f"Blocked: this brief was already delegated. "
                f"Delegate script {nxt}/10: write workspace/scripts/tool{nxt}.ps1"
            )
        if delegate_count >= 1 and write_count < delegate_count:
            return "Blocked: workspace is still working — wait for write_file before re-delegating."
    if tool_name == "report_generate" and write_count == 0:
        return "Blocked: no scripts written yet — delegate_to workspace and write_file first."
    return None


# ──────────────────────────────────────────────────────────────────────────────
# VibeThinker reformulation fallback
# ──────────────────────────────────────────────────────────────────────────────

def build_vt_reformulation_prompt(
    failed_content: str,
    tool_catalog_excerpt: str,
    user_context: str = "",
) -> list[dict[str, str]]:
    """Build messages to ask VibeThinker to reformulate a failed instruction.

    Called when the parser found no tool calls AND the harness found no
    malformed calls. VibeThinker re-expresses the intended action as a
    precise instruction that qwen-coder can parse correctly.

    Returns a messages list ready for an Ollama chat call.
    """
    system = (
        "You are VibeThinker, the planning model for an AI agent. "
        "The executor model (qwen-coder) emitted prose instead of a tool call. "
        "Your job: re-express the intended action as a SINGLE precise instruction "
        "that qwen-coder can execute immediately. "
        "State: which tool to call, what arguments to pass, and why. "
        "Speak in first person. Be direct and specific. "
        "End with an exact example of the correct tool call format."
    )
    user = (
        f"The executor model's failed output:\n{(failed_content or '').strip()[:400]}\n\n"
        f"User's original request: {(user_context or '').strip()[:300]}\n\n"
        f"Available tools (excerpt):\n{tool_catalog_excerpt[:600]}\n\n"
        "Reformulate: what should the executor model do next, and exactly how?"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user",   "content": user},
    ]


def hard_action_nudge(user_context: str = "", session_id: str | None = None) -> str:
    """Directive injected when reflection loop exhausts without producing tool calls.

    This is a last-resort format nudge. It no longer hardcodes domain-specific
    tool calls; instead it prompts the model to emit any valid tool call from
    the registered set.
    """
    return (
        "[SYSTEM DIRECTIVE] Prose is not execution. "
        "Emit EXACTLY one tool call NOW using <tool_call> tags — "
        "no summaries, no [STATUS] lines, no explanations.\n"
        "Format:\n"
        "<tool_call>\n"
        '{"name": "<tool_name>", "arguments": {<args>}}\n'
        "</tool_call>"
    )


# ──────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────────────────

def _handoff_return_call(user_context: str, session_id: str | None = None) -> dict[str, Any]:
    """Salvage LEAD action after specialist handoff — update status, do not re-delegate."""
    path = "workspace/status.md"
    if session_id:
        path = f"workspace/sessions/{session_id}/status_{session_id}.md"
    line = f"Specialist returned — next: continue {(user_context or 'mission').strip()[:120]}"
    return {
        "function": {
            "name": "append_note",
            "arguments": {"path": path, "line": line},
        }
    }
