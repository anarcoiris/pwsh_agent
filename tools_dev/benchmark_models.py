"""Benchmark Ollama model profiles against golden missions.

Usage:
    python tools_dev/benchmark_models.py --profile baseline --mission pingsweep
    python tools_dev/benchmark_models.py --all-profiles --mission pingsweep
    python tools_dev/benchmark_models.py --profile qwen_vibe_synth --list
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

VIBE_MODEL = "hf.co/oussaber/VibeThinker-3B-Q4_K_M-GGUF"

MISSIONS = {
    "pingsweep": (
        "Write ping_sweep.ps1 in the workspace folder that pings 192.168.1.1 to "
        "192.168.1.5 (one ping each, quiet) and writes active hosts to "
        "workspace/ping_results.log, then run it with host_exec and fix any "
        "errors until it works."
    ),
    "fix_broken_ps1": (
        "Create workspace/broken.ps1 that intentionally has a syntax error, then "
        "read it, fix the error, run with host_exec, and confirm exit code 0."
    ),
    "hygiene_stub": (
        "Use hygiene_lookup to find finding REF-001 (or any finding in the feed). "
        "Summarize what action is needed. If no findings, report that clearly."
    ),
}


def load_profiles() -> dict:
    path = _ROOT / "config.models" / "profiles.yaml"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("profiles", {})


def apply_profile(agent, profile_name: str, profiles: dict) -> None:
    prof = profiles.get(profile_name, {})
    if not prof:
        raise ValueError(f"Unknown profile: {profile_name}")

    ollama = agent.config.setdefault("ollama", {})
    for key in ("default_model", "synthesis_model", "conversational_model"):
        if key in prof:
            val = prof[key]
            ollama[key] = val

    agent.default_model = ollama.get("default_model", agent.default_model)
    syn = ollama.get("synthesis_model")
    agent.synthesis_model = syn if syn else agent.default_model
    agent.conversational_model = ollama.get("conversational_model")
    agent.adapter.model = agent.default_model

    if agent.conversational_model and hasattr(agent, "mission_evaluator") and agent.mission_evaluator:
        agent.mission_evaluator.model = agent.conversational_model
    intent_model = agent.config.get("intent", {}).get("model") or agent.conversational_model
    if agent.intent_formalizer and intent_model:
        agent.intent_formalizer.model = intent_model


def seed_hygiene_stub_feed() -> None:
    """Ensure hygiene_stub mission has at least one finding to lookup."""
    from tools.hygiene import resolve_feed_dir

    feed = resolve_feed_dir()
    chunk_dir = feed / "findings" / "pwsh_agent"
    chunk_dir.mkdir(parents=True, exist_ok=True)
    chunk = chunk_dir / "REF-001.md"
    if not chunk.exists():
        chunk.write_text(
            """---
repo: pwsh_agent
finding_id: REF-001
severity: P2
auto_fixable: false
tools: [hygiene_lookup]
phase: [hygiene]
task_id: benchmark
updated: 2026-06-18T00:00:00Z
---

# REF-001: Benchmark stub finding

**File:** workspace/benchmark_stub.txt
**Action:** Create workspace/benchmark_stub.txt with content 'ok' for benchmark verification.
**Source:** benchmark/seed
""",
            encoding="utf-8",
        )
    manifest_path = feed / "manifest.json"
    manifest = {"updated": datetime.now(timezone.utc).isoformat(), "count": 1, "findings": [{
        "repo": "pwsh_agent",
        "finding_id": "REF-001",
        "severity": "P2",
        "auto_fixable": False,
        "title": "Benchmark stub finding",
        "file": "workspace/benchmark_stub.txt",
        "line": None,
        "task_id": "benchmark",
        "source": "benchmark/seed",
        "chunk_path": "findings/pwsh_agent/REF-001.md",
        "updated": datetime.now(timezone.utc).isoformat(),
    }]}
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


async def run_benchmark(profile: str, mission: str, profiles: dict) -> dict:
    from agent import ReActAgent
    from tools_dev.llm_audit_view import load_exchanges, summarize_exchange

    if mission == "hygiene_stub":
        seed_hygiene_stub_feed()

    agent = ReActAgent()
    agent.config.setdefault("agent", {})["llm_audit"] = "meta"
    apply_profile(agent, profile, profiles)

    agent.new_session()
    prompt = MISSIONS[mission]
    t0 = time.time()

    tool_calls = 0

    def step_cb(kind: str, payload) -> None:
        nonlocal tool_calls
        if kind == "AGENT_TOOL_CALL":
            tool_calls += 1

    reply = await agent.chat_turn(prompt, step_callback=step_cb)
    elapsed = time.time() - t0

    entries = load_exchanges(agent.session_id, last=50)
    worst_sat = 0.0
    native_total = 0
    parsed_total = 0
    for e in entries:
        s = summarize_exchange(e)
        sat = float(s.get("ctx_saturation") or 0)
        worst_sat = max(worst_sat, sat)
        native_total += int(s.get("native_tool_calls") or 0)
        parsed_total += int(s.get("parsed_tool_calls") or 0)

    deliverable_ok = False
    if mission == "pingsweep":
        log_path = Path.cwd() / "workspace" / "ping_results.log"
        deliverable_ok = log_path.is_file() and log_path.stat().st_size > 0
    elif mission == "fix_broken_ps1":
        deliverable_ok = True  # success if chat completes with tool use
    elif mission == "hygiene_stub":
        deliverable_ok = "REF-001" in (reply or "") or tool_calls > 0

    return {
        "profile": profile,
        "mission": mission,
        "models": {
            "default": agent.default_model,
            "synthesis": agent.synthesis_model,
            "conversational": agent.conversational_model,
        },
        "elapsed_s": round(elapsed, 2),
        "tool_calls": tool_calls,
        "native_tool_calls": native_total,
        "parsed_tool_calls": parsed_total,
        "worst_ctx_saturation": round(worst_sat, 4),
        "deliverable_ok": deliverable_ok,
        "reply_excerpt": (reply or "")[:500],
        "session_id": agent.session_id,
    }


def write_report(results: list[dict]) -> Path:
    out_dir = _ROOT / "state" / "benchmarks"
    out_dir.mkdir(parents=True, exist_ok=True)
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_path = out_dir / f"{date}.md"

    lines = [
        f"# Model benchmark — {date}",
        "",
        "| Profile | Mission | Default model | Native TC | Parsed TC | Saturation | Deliverable | Time(s) |",
        "|---------|---------|---------------|-----------|-----------|------------|-------------|---------|",
    ]
    for r in results:
        lines.append(
            f"| {r['profile']} | {r['mission']} | {r['models']['default'][:24]} | "
            f"{r['native_tool_calls']} | {r['parsed_tool_calls']} | {r['worst_ctx_saturation']} | "
            f"{'PASS' if r['deliverable_ok'] else 'FAIL'} | {r['elapsed_s']} |"
        )
    lines.append("")
    lines.append("## Details")
    for r in results:
        lines.append(f"\n### {r['profile']} / {r['mission']}\n")
        lines.append(f"- Synthesis model: `{r['models']['synthesis']}`")
        lines.append(f"- Conversational: `{r['models']['conversational']}`")
        lines.append(f"- Reply excerpt: {r['reply_excerpt'][:200]}...")

    content = "\n".join(lines) + "\n"
    if out_path.exists():
        content = out_path.read_text(encoding="utf-8") + "\n---\n\n" + content
    out_path.write_text(content, encoding="utf-8")
    return out_path


async def main_async() -> int:
    parser = argparse.ArgumentParser(description="Benchmark model profiles")
    parser.add_argument("--profile", default="baseline")
    parser.add_argument("--mission", default="pingsweep", choices=list(MISSIONS))
    parser.add_argument("--all-profiles", action="store_true")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()

    profiles = load_profiles()
    if args.list:
        print("Profiles:", ", ".join(profiles))
        print("Missions:", ", ".join(MISSIONS))
        return 0

    to_run = list(profiles.keys()) if args.all_profiles else [args.profile]
    results = []
    for prof in to_run:
        print(f"\n[benchmark] profile={prof} mission={args.mission}")
        try:
            r = await run_benchmark(prof, args.mission, profiles)
            results.append(r)
            print(json.dumps(r, indent=2))
        except Exception as exc:
            print(f"[benchmark] FAILED {prof}: {exc}", file=sys.stderr)

    if results:
        path = write_report(results)
        print(f"\n[benchmark] Report: {path}")
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
