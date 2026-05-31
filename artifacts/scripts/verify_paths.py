"""Verify artifact paths and routing after repo cleanse."""
import argparse
import sys
from pathlib import Path

from repo_bootstrap import bootstrap

bootstrap()

from core.debug_log import debug_log
from core.llm_utils import DynamicContextBuilder
from core.context_router import ContextRouter
from core.runtime_paths import app_root, artifacts_captures_dir, search_roots
from core.task_intent import TaskIntentExtractor
import tools

USER_MSG = (
    "locate a file named last_capture.pcapng. We want to decode (maybe using tshark) "
    "the http packets and try to find packets containing 'login', 'xml', 'xmlobj' "
    "to analyze them. Using Ethernet interface"
)

ROOT = app_root()


def main():
    parser = argparse.ArgumentParser(description="Verify artifact paths and routing.")
    parser.add_argument(
        "--skip-analyze",
        action="store_true",
        help="Skip analyze_pcapng (avoids spawning tshark subprocess windows).",
    )
    args = parser.parse_args()
    run_id = "post-fix"
    # H-A: artifact locations on disk (app_root, not artifacts/)
    candidates = [
        ROOT / "last_capture.pcapng",
        ROOT / "workspace" / "last_capture.pcapng",
        artifacts_captures_dir() / "last_capture.pcapng",
    ]
    found = {str(p): p.exists() for p in candidates}
    debug_log("verify_paths.py:main", "artifact candidates", {"found": found, "roots": [str(r) for r in search_roots()]}, "A", run_id)

    # H-B: find_file in registry
    registry_has_find_file = hasattr(tools, "find_file") and callable(tools.find_file)
    schema_names = [t["function"]["name"] for t in tools.TOOLS_SCHEMA if "function" in t]
    ff = tools.find_file("last_capture.pcapng") if registry_has_find_file else {}
    debug_log(
        "verify_paths.py:main",
        "find_file registry",
        {
            "in_schema": "find_file" in schema_names,
            "in_tools": registry_has_find_file,
            "find_result": ff,
        },
        "B",
        run_id,
    )

    # H-C: phase routing for pcap query
    messages = [{"role": "user", "content": USER_MSG}]
    phase = DynamicContextBuilder.build_context(messages)
    intent = TaskIntentExtractor.parse(USER_MSG)
    injections = ContextRouter.build_injections(messages, intent)
    debug_log(
        "verify_paths.py:main",
        "phase routing",
        {
            "phase_hint_head": phase[:120] if phase else "",
            "is_dev_task": intent.is_dev_task,
            "deliverables": intent.deliverables,
            "injection_count": len(injections),
        },
        "C",
        run_id,
    )

    # H-D: resolve_project_file from each search root
    from core.artifacts import resolve_project_file

    resolved = resolve_project_file("last_capture.pcapng")
    debug_log(
        "verify_paths.py:main",
        "resolve basename",
        {"resolved": str(resolved) if resolved else None, "exists": bool(resolved and resolved.exists())},
        "D",
        run_id,
    )

    # H-E: analyze_pcapng on resolved path (optional — tshark spawns child consoles on Windows)
    if not args.skip_analyze and resolved and resolved.exists():
        res = tools.analyze_pcapng(str(resolved), filter_expression="http", limit=3)
        debug_log(
            "verify_paths.py:main",
            "analyze resolved pcap",
            {"success": res.get("success"), "error": (res.get("error") or "")[:200]},
            "E",
            run_id,
        )
    else:
        debug_log(
            "verify_paths.py:main",
            "analyze skipped",
            {"skip_analyze": args.skip_analyze, "resolved": str(resolved) if resolved else None},
            "E",
            run_id,
        )

    print("Verification complete — see debug-a918e8.log")


if __name__ == "__main__":
    main()
