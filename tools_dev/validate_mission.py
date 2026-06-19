"""Run a validation chat turn against live Ollama and report audit telemetry.

Usage:
    python tools_dev/validate_mission.py router     # router login mission
    python tools_dev/validate_mission.py pingsweep  # ping sweep script mission
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Windows console defaults to cp1252 — tool output may contain arbitrary bytes.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

MISSIONS = {
    "router": (
        "Login to http://192.168.1.1 with user 'user' and password '321123Aa!'. "
        "Fetch the page first and inspect how the login works before attempting."
    ),
    "pingsweep": (
        "Write ping_sweep.ps1 in the workspace folder that pings 192.168.1.1 to "
        "192.168.1.5 (one ping each, quiet) and writes active hosts to "
        "workspace/ping_results.log, then run it with host_exec and fix any "
        "errors until it works."
    ),
    "continue": (
        "Continue: run workspace/ping_sweep.ps1 with host_exec and fix any "
        "errors until workspace/ping_results.log contains the active hosts."
    ),
}


def step_cb(kind: str, payload) -> None:
    if kind == "AGENT_TOOL_CALL":
        print(f"  -> TOOL {payload.get('tool')}: {str(payload.get('args'))[:160]}")
    elif kind == "AGENT_TOOL_RESULT":
        res = payload.get("result")
        print(f"  <- RESULT {payload.get('tool')}: {str(res)[:200]}")
    elif kind == "AGENT_STATUS":
        print(f"  .. {payload}")


async def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else "router"
    message = MISSIONS[name]

    from agent import ReActAgent

    agent = ReActAgent()
    if name != "continue":
        agent.new_session()
    print(f"[validate] mission={name} session={agent.session_id}")
    print(f"[validate] prompt: {message}")

    reply = await agent.chat_turn(message, step_callback=step_cb)
    print("\n[validate] FINAL REPLY:\n" + (reply or "")[:2000])

    # Telemetry summary from llm_audit.jsonl
    from tools_dev.llm_audit_view import load_exchanges, summarize_exchange

    entries = load_exchanges(agent.session_id, last=50)
    print(f"\n[validate] llm_audit entries: {len(entries)}")
    worst = 0.0
    for e in entries:
        s = summarize_exchange(e)
        sat = s.get("ctx_saturation") or 0
        worst = max(worst, float(sat))
        print(
            f"  prompt_tok={s['prompt_eval_count']} gen_tok={s['eval_count']} "
            f"sat={sat} native={s['native_tool_calls']} parsed={s['parsed_tool_calls']} "
            f"paths={','.join(s['parser_paths']) or '-'} latency={s['latency_ms']}ms"
        )
    print(f"[validate] worst ctx_saturation: {worst}")
    if worst >= 0.95:
        print("[validate] WARNING: context saturated — input was likely truncated")

    # Artifacts present?
    from core.session_paths import session_artifacts_dir

    arts = sorted(session_artifacts_dir(agent.session_id).glob("*"))
    print(f"[validate] artifacts: {[a.name for a in arts]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
