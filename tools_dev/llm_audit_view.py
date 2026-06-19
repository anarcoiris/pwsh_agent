"""Viewer for state/sessions/<id>/llm_audit.jsonl.

Usage:
    python tools_dev/llm_audit_view.py                 # last exchange, active session
    python tools_dev/llm_audit_view.py --last 5        # last 5 exchanges
    python tools_dev/llm_audit_view.py --session 20260603_234404 --last 3
    python tools_dev/llm_audit_view.py --full          # include full message bodies

Shows per exchange: token usage (prompt_eval_count vs num_ctx saturation),
native vs parsed tool calls, parser fallback paths, and a per-message
role/chars table with injection blocks highlighted.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import deque
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.session_paths import load_active_session_id, sessions_state_root  # noqa: E402

_INJECTION_MARKERS = (
    "### CURRENT STATE",
    "### RELATED TOOL SCHEMAS",
    "### AGENTS",
    "### SOUL",
    "### TOOLS",
    "### TOOL PLAYBOOKS",
    "### SESSION CONTEXT",
    "### PLAN",
)


def _classify(content: str) -> str:
    head = (content or "").lstrip()[:60]
    for marker in _INJECTION_MARKERS:
        if head.startswith(marker):
            return marker.strip("# ").strip()
    return ""


def load_exchanges(session_id: str | None = None, last: int = 1) -> list[dict]:
    """Return the last N audit entries for a session (active session default)."""
    sid = session_id or load_active_session_id()
    path = sessions_state_root() / sid / "llm_audit.jsonl"
    if not path.is_file():
        return []
    tail: deque[str] = deque(maxlen=max(1, last))
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if line:
                tail.append(line)
    out: list[dict] = []
    for line in tail:
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def summarize_exchange(entry: dict) -> dict:
    """Normalize a full- or meta-mode entry into a render-friendly dict."""
    messages = entry.get("messages")
    if messages is not None:
        msg_meta = [
            {
                "role": m.get("role", "?"),
                "chars": len(str(m.get("content", ""))),
                "kind": _classify(str(m.get("content", ""))),
                "preview": str(m.get("content", "")).replace("\n", " ")[:120],
            }
            for m in messages
        ]
        tools_count = len(entry.get("tools_schema") or [])
        response = str(entry.get("response", ""))
    else:
        msg_meta = [
            {
                "role": m.get("role", "?"),
                "chars": m.get("chars", 0),
                "kind": "",
                "preview": "",
            }
            for m in entry.get("message_meta", [])
        ]
        tools_count = entry.get("tools_schema_count", 0)
        response = str(entry.get("response_preview", ""))

    prompt_tok = entry.get("prompt_eval_count")
    num_ctx = entry.get("num_ctx")
    saturation = entry.get("ctx_saturation")
    if saturation is None and prompt_tok and num_ctx:
        saturation = round(prompt_tok / num_ctx, 3)

    return {
        "timestamp": entry.get("timestamp"),
        "model": entry.get("model", ""),
        "latency_ms": entry.get("latency_ms"),
        "prompt_eval_count": prompt_tok,
        "eval_count": entry.get("eval_count"),
        "num_ctx": num_ctx,
        "ctx_saturation": saturation,
        "native_tool_calls": entry.get("native_tool_calls"),
        "parsed_tool_calls": entry.get("parsed_tool_calls"),
        "parser_paths": entry.get("parser_paths") or [],
        "num_messages": entry.get("num_messages", len(msg_meta)),
        "prompt_chars": entry.get("prompt_chars"),
        "tools_schema_count": tools_count,
        "message_meta": msg_meta,
        "response": response,
    }


def render_exchange(summary: dict, *, full_response: bool = False) -> str:
    sat = summary.get("ctx_saturation")
    sat_flag = ""
    if isinstance(sat, (int, float)):
        if sat >= 0.95:
            sat_flag = "  << CONTEXT SATURATED (input likely truncated)"
        elif sat >= 0.80:
            sat_flag = "  << approaching num_ctx"

    lines = [
        "=" * 78,
        f"model={summary['model']}  latency={summary['latency_ms']}ms  "
        f"tokens: prompt={summary['prompt_eval_count']} gen={summary['eval_count']} "
        f"ctx={summary['num_ctx']} sat={sat}{sat_flag}",
        f"tool_calls: native={summary['native_tool_calls']} "
        f"parsed={summary['parsed_tool_calls']} "
        f"paths={','.join(summary['parser_paths']) or '-'}  "
        f"tools_schema={summary['tools_schema_count']}  "
        f"messages={summary['num_messages']} ({summary['prompt_chars']} chars)",
        "-" * 78,
    ]
    for i, m in enumerate(summary["message_meta"]):
        kind = f" [{m['kind']}]" if m["kind"] else ""
        preview = f"  | {m['preview']}" if m["preview"] else ""
        lines.append(f"{i:>3} {m['role']:<9} {m['chars']:>7} ch{kind}{preview}")
    lines.append("-" * 78)
    resp = summary["response"]
    if not full_response and len(resp) > 600:
        resp = resp[:600] + f"... [{len(summary['response'])} chars total]"
    lines.append("RESPONSE: " + resp.replace("\n", "\n          "))
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Inspect llm_audit.jsonl exchanges")
    ap.add_argument("--session", default=None, help="Session id (default: active)")
    ap.add_argument("--last", type=int, default=1, help="Number of exchanges")
    ap.add_argument("--full", action="store_true", help="Print full response bodies")
    args = ap.parse_args()

    entries = load_exchanges(args.session, args.last)
    if not entries:
        sid = args.session or load_active_session_id()
        print(f"No llm_audit.jsonl entries for session {sid}")
        return 1
    for entry in entries:
        print(render_exchange(summarize_exchange(entry), full_response=args.full))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
