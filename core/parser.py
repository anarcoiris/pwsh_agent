"""
core/parser.py — Unified LLM output parser for Pulse Windows Agent.

Ported and adapted from MCP_Pentesting/core/agent_parser.py +
the CodeBlockExtractor in llm_utils.py.

Changes from father:
- CodeBlockExtractor maps powershell/ps1/cmd/batch blocks → host_exec
  (replaces Kali's bash/sh → kali_exec)
- Recursive extractor handles both {name, arguments} and {function: {name, arguments}}
"""

import json
import re
from typing import Any

def _fix_invalid_json_escapes(s: str) -> str:
    """Fix common invalid JSON escapes like \; or \' by double escaping them."""
    # Matches \ followed by anything that isn't a valid JSON escape char
    return re.sub(r'\\([^nrt\\"/ubf])', r'\\\\\1', s)


def _try_parse_json(s: str) -> Any | None:
    """Parse JSON with escape-fix fallback."""
    s = s.strip()
    if not s:
        return None
    for candidate in (s, _fix_invalid_json_escapes(s)):
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
    return None


def _extract_json_objects(content: str) -> list[Any]:
    """Extract top-level {...} blobs from text (handles nested braces)."""
    results: list[Any] = []
    i = 0
    while i < len(content):
        if content[i] != "{":
            i += 1
            continue
        depth = 0
        start = i
        in_str = False
        esc = False
        closed = False
        for j in range(i, len(content)):
            c = content[j]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            else:
                if c == '"':
                    in_str = True
                elif c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        parsed = _try_parse_json(content[start : j + 1])
                        if parsed is not None:
                            results.append(parsed)
                        i = j + 1
                        closed = True
                        break
        if not closed:
            break
    return results


_CODE_LANG_EXT = {
    "python": ".py",
    "py": ".py",
    "powershell": ".ps1",
    "ps1": ".ps1",
    "ps": ".ps1",
    "cmd": ".cmd",
    "batch": ".bat",
    "bat": ".bat",
}
_SKIP_CODE_LANGS = {"json", "tool_call", "markdown", "md", "text", ""}

_REGISTERED_TOOLS = frozenset({
    "append_note", "find_file", "analyze_pcapng", "host_exec", "run_script",
    "read_file", "write_file", "sequentialthinking", "capture_packets",
    "list_network_interfaces", "crack_hash", "system_info", "port_scan",
    "ping_sweep", "dns_lookup",
})

_TOOL_NAME_JSON = re.compile(
    r"\b(" + "|".join(_REGISTERED_TOOLS) + r")\s+(\{.*\})\s*$",
    re.I | re.DOTALL,
)


def _infer_script_path(code: str, user_context: str, ext: str) -> str:
    """Guess target path from code comments or recent user messages."""
    for pat in (
        r"(?:#\s*(?:file|path):\s*(\S+))",
        r'(?:"""[^"]*save(?:\s+as)?:\s*(\S+))',
        r"(?:save (?:to|as|in) ['\"]?([\w./\\-]+\.(?:py|ps1|md|txt)))",
    ):
        m = re.search(pat, code, re.I)
        if m:
            return m.group(1).replace("\\", "/")

    ctx = user_context or ""

    folder_m = re.search(r"(?:in|to|under)\s+(?:the\s+)?([\w.-]+)\s+folder", ctx, re.I)
    script_m = re.search(r"\b([\w.-]+\.(?:py|ps1))\b", ctx, re.I)
    if folder_m and script_m:
        return f"{folder_m.group(1)}/{script_m.group(1)}"

    m = re.search(r"([\w./\\-]+\.(?:py|ps1|md|txt))", ctx, re.I)
    if m:
        return m.group(1).replace("\\", "/")

    if script_m:
        return script_m.group(1)

    return f"workspace/script{ext}"


def _extract_code_block_tool_calls(content: str, user_context: str = "") -> list[dict]:
    """Path 5 — ```python / ```powershell fenced blocks → write_file."""
    calls: list[dict] = []
    for m in re.finditer(r"```(\w+)?\s*(.*?)\s*```", content, re.DOTALL | re.IGNORECASE):
        lang = (m.group(1) or "").lower()
        code = m.group(2).strip()
        if not code or lang in _SKIP_CODE_LANGS:
            continue
        ext = _CODE_LANG_EXT.get(lang)
        if not ext:
            continue
        path = _infer_script_path(code, user_context, ext)
        calls.append({
            "function": {
                "name": "write_file",
                "arguments": {"path": path, "content": code},
            }
        })
    return calls


def _pick_best_tool_calls(calls: list[dict], limit: int = 1) -> list[dict]:
    """Prefer actionable tools over sequentialthinking; enforce one call per turn."""
    if not calls:
        return []
    priority = {
        "write_file": 0, "read_file": 1, "find_file": 2, "run_script": 3,
        "analyze_pcapng": 4, "host_exec": 5,
        "finding_create": 6, "report_generate": 7,
        "sequentialthinking": 50,
    }

    def sort_key(tc: dict) -> tuple:
        name = tc.get("function", tc).get("name", "")
        return (priority.get(name, 10), name)

    ranked = sorted(calls, key=sort_key)
    return ranked[:limit]


class AgentOutputParser:
    """
    Unified engine to extract content, reasoning, and tool calls from a
    raw LLM message dict.  Tries 5 fallback paths in order:
      1. Native Ollama tool_calls in the message payload
      2. <tool_call>…</tool_call> XML tags (model template format)
      3. ```json…``` markdown code blocks
      4. Bare inline {"name":…,"arguments":…} JSON
      5. ```python / ```powershell code blocks → write_file
    """

    def __init__(self, tools_registry: dict[str, Any]):
        self.tools_registry = tools_registry
        self._user_context: str = ""

    def set_user_context(self, user_context: str) -> None:
        self._user_context = user_context or ""

    # ──────────────────────────────────────────────────────────────────────
    # Public entry-point
    # ──────────────────────────────────────────────────────────────────────

    def process_llm_output(self, message: dict) -> tuple[str, str, list[dict]]:
        """
        Returns (content, reasoning, tool_calls).

        • content   — raw text content of the message
        • reasoning — extracted 🧠 Reasoning: block (if any)
        • tool_calls — list of normalised {function: {name, arguments}} dicts
        """
        content = message.get("content", "") or ""
        reasoning = ""

        # Extract explicit reasoning block
        match = re.search(
            r"🧠\s*Reasoning:\s*(.*?)(?=\n\n|```|<tool_call>|$)",
            content,
            re.DOTALL,
        )
        if match:
            reasoning = match.group(1).strip()

        # Native Ollama tool_calls (already structured)
        tool_calls = message.get("tool_calls", [])

        # Fallback: scan text content for tool calls
        if not tool_calls:
            tool_calls = self._discover_and_extract_tool_calls(
                content, user_context=self._user_context
            )

        return content, reasoning, _pick_best_tool_calls(tool_calls, limit=1)

    # ──────────────────────────────────────────────────────────────────────
    # Discovery engine (5 fallback paths)
    # ──────────────────────────────────────────────────────────────────────

    def discover_tool_calls(self, content: str, user_context: str = "") -> list[dict]:
        """Public wrapper used by adapter and salvage paths."""
        return _pick_best_tool_calls(
            self._discover_and_extract_tool_calls(content, user_context=user_context),
            limit=1,
        )

    def _discover_and_extract_tool_calls(
        self, content: str, user_context: str = ""
    ) -> list[dict]:
        blocks: list[Any] = []

        # Path 2b — tool_name {"arg": "val"} prose (model omits name/arguments wrapper)
        for m in _TOOL_NAME_JSON.finditer(content):
            tool_name = m.group(1)
            parsed = _try_parse_json(m.group(2).strip())
            if parsed is not None and isinstance(parsed, dict):
                blocks.append({"name": tool_name, "arguments": parsed})

        # Path 2 — <tool_call>…</tool_call> tags
        for raw in re.findall(
            r"<tool_call>\s*(.*?)\s*</tool_call>", content, re.DOTALL
        ):
            for line in raw.strip().splitlines():
                line = line.strip()
                if line:
                    parsed = _try_parse_json(line)
                    if parsed is not None:
                        blocks.append(parsed)

        # Path 3 — ```json … ``` or ``` … ``` fenced blocks
        for raw in re.findall(
            r"```(?:json|tool_call)?\s*(.*?)\s*```", content, re.DOTALL | re.IGNORECASE
        ):
            parsed = _try_parse_json(raw.strip())
            if parsed is not None:
                blocks.append(parsed)
            else:
                for line in raw.strip().splitlines():
                    line = line.strip()
                    if line:
                        parsed = _try_parse_json(line)
                        if parsed is not None:
                            blocks.append(parsed)

        # Path 4 — bare JSON / inline {"name":…,"arguments":…} objects
        stripped = content.strip()
        if stripped.startswith("{"):
            parsed = _try_parse_json(stripped)
            if parsed is not None:
                blocks.append(parsed)
        for obj in _extract_json_objects(content):
            if obj not in blocks:
                blocks.append(obj)

        # Recursively extract normalised {function: {name, arguments}} dicts
        all_calls: list[dict] = []
        for b in blocks:
            all_calls.extend(self._extract_tool_calls_recursive(b))

        # Path 5 — ```python / ```powershell code blocks → write_file
        all_calls.extend(_extract_code_block_tool_calls(content, user_context))

        return all_calls

    # ──────────────────────────────────────────────────────────────────────
    # Recursive structure normaliser
    # ──────────────────────────────────────────────────────────────────────

    def _extract_tool_calls_recursive(self, data: Any) -> list[dict]:
        """
        Recursively walk any JSON structure and normalise all tool-call
        shapes into {function: {name: str, arguments: dict}}.
        """
        found: list[dict] = []

        if isinstance(data, list):
            for item in data:
                found.extend(self._extract_tool_calls_recursive(item))

        elif isinstance(data, dict):
            # Shape A: {name: …, arguments: …}  (Ollama text-fallback format)
            if "name" in data and "arguments" in data:
                found.append(
                    {
                        "function": {
                            "name": data["name"],
                            "arguments": data["arguments"],
                        }
                    }
                )
            # Shape B: {function: {name: …, arguments: …}}  (OpenAI format)
            elif "function" in data and isinstance(data["function"], dict):
                found.append({"function": data["function"]})
            # Shape C: top-level tool_calls list
            elif "tool_calls" in data:
                found.extend(self._extract_tool_calls_recursive(data["tool_calls"]))
            # Recurse into values
            else:
                for v in data.values():
                    found.extend(self._extract_tool_calls_recursive(v))

        return found

    def salvage_tool_call(self, content: str, user_context: str = "") -> dict | None:
        """
        Attempt to recover a single tool call from raw LLM text output.
        Used by RetryOrchestrator when the main parse path missed a call.
        """
        calls = self._discover_and_extract_tool_calls(content, user_context=user_context)
        if not calls:
            return None
        tc = _pick_best_tool_calls(calls, limit=1)[0]
        func = tc.get("function", tc)
        name = func.get("name", "")
        args = func.get("arguments", {})
        if isinstance(args, str):
            args = _try_parse_json(args) or {}
        if not isinstance(args, dict):
            args = {}
        if name and name in self.tools_registry:
            return {"function": {"name": name, "arguments": args}}
        return None
