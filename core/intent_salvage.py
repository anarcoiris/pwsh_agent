"""Map prose LLM output + user context to concrete tool calls when the parser misses."""

from __future__ import annotations

import re
from typing import Any

# Model emits status prose instead of tool calls — treat as stall, not completion.
PROSE_STALL_RE = re.compile(
    r"\[SYSTEM\]\s*Task complete|\[STATUS\]\s*MISSION_|\*\*Next Steps:\*\*|\*\*Final Thoughts:\*\*",
    re.I | re.M,
)

_SEARCH_INTENT_RE = re.compile(
    r"\b(expand.*search|search.*term|grep|filter|password|xml|xmlobj|login|salt|verbose|credential|"
    r"analyze.*filter|complete.*previous|previous task)\b",
    re.I,
)

_DISPLAY_FACTS_RE = re.compile(
    r"\b(show|display|tell|list|what are|give me)\b.*\b(facts?|hash|salt|cracked|credential)\b",
    re.I,
)

_HASH_CRACK_RE = re.compile(
    r"(crack.*hash|hash.*crack|brute.*force|password.*hash|\bcrack_hash\b|"
    r"crack.*sha-?256|sha-?256.*crack|\bhaspro\b|\bhashpro\b|\bhash_pro7\b)",
    re.I,
)

_PCAP_OR_EXTRACT_RE = re.compile(
    r"(\.pcapng|\.pcap\b|\btshark\b|\bwireshark\b|last_capture|decode.*packet|"
    r"analyze.*packet|http packet|login.*packet|locate.*pcap|login_forms\.txt|"
    r"pwd(?:_[\d]+)?\.txt|credentials?\.txt|extract.*(?:pcap|password|salt))",
    re.I,
)


def looks_like_prose_stall(content: str) -> bool:
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


def _find_and_grep_call(pattern: str | None = None) -> dict[str, Any]:
    return {
        "function": {
            "name": "find_and_grep",
            "arguments": {
                "pattern": pattern or "password|Password|Username|xml|xmlObj|616a6178|login",
                "path_glob": ".pulse/pcap_logs/verbose_*.txt",
                "max_files": 10,
                "case_insensitive": True,
            },
        }
    }


def salvage_intent_tool_call(
    raw_content: str,
    user_context: str = "",
    *,
    session_id: str | None = None,
) -> dict[str, Any] | None:
    """
    Infer a concrete tool call from user intent when the model emitted prose only.
    Used by parser_reflection before falling back to sequentialthinking.
    """
    combined = f"{user_context} {raw_content}".lower()

    # Simple hash cracking prompts without PCAP/extraction intent should never trigger grep salvage
    if _HASH_CRACK_RE.search(combined) and not _PCAP_OR_EXTRACT_RE.search(combined):
        return None

    if _DISPLAY_FACTS_RE.search(combined):
        paths = []
        if session_id:
            paths.append(f"state/sessions/{session_id}/facts.json")
        paths.extend(["state/sessions/*/facts.json", "workspace/sessions/*/login_forms.txt"])
        for path in paths:
            if "*" not in path:
                return {"function": {"name": "read_file", "arguments": {"path": path}}}
        return {"function": {"name": "find_file", "arguments": {"name": "facts.json"}}}

    if _SEARCH_INTENT_RE.search(combined):
        return _find_and_grep_call()

    return None


def hard_action_nudge(user_context: str = "", session_id: str | None = None) -> str:
    """Directive with exact tool_call format when reflection loop exhausts."""
    salvaged = salvage_intent_tool_call("", user_context, session_id=session_id)
    if salvaged:
        import json

        name = salvaged["function"]["name"]
        args = json.dumps(salvaged["function"]["arguments"], indent=2)
        return (
            "[SYSTEM DIRECTIVE] Prose is not execution. Emit EXACTLY one tool call NOW "
            "using <tool_call> tags — no summaries, no [STATUS] lines.\n"
            f"<tool_call>\n{{\"name\": \"{name}\", \"arguments\": {args}}}\n</tool_call>"
        )
    return (
        "[SYSTEM DIRECTIVE] Prose is not execution. Emit EXACTLY one tool call NOW "
        "using <tool_call> tags — no summaries, no [STATUS] lines.\n"
        "<tool_call>\n"
        '{"name": "find_and_grep", "arguments": {"pattern": "password|Password|Username|xml|xmlObj|login", '
        '"path_glob": ".pulse/pcap_logs/verbose_*.txt", "max_files": 10, "case_insensitive": true}}\n'
        "</tool_call>"
    )
