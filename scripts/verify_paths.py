"""Verify artifact paths and routing after repo cleanse."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.debug_log import debug_log
from core.llm_utils import DynamicContextBuilder
from core.context_router import ContextRouter
from core.task_intent import TaskIntentExtractor
import tools

USER_MSG = (
    "locate a file named last_capture.pcapng. We want to decode (maybe using tshark) "
    "the http packets and try to find packets containing 'login', 'xml', 'xmlobj' "
    "to analyze them. Using Ethernet interface"
)

ROOT = Path(__file__).resolve().parent.parent


def main():
    run_id = "post-fix"
    # H-A: artifact locations on disk
    candidates = [
        ROOT / "last_capture.pcapng",
        ROOT / "workspace" / "last_capture.pcapng",
        ROOT / "artifacts" / "captures" / "last_capture.pcapng",
        ROOT / "network_logs" / "last_capture.pcapng",
    ]
    found = {str(p): p.exists() for p in candidates}
    debug_log("verify_paths.py:main", "artifact candidates", {"found": found}, "A", run_id)

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

    # H-D: analyze_pcapng relative path resolution (cwd-dependent)
    for rel in ("last_capture.pcapng", "network_logs/last_capture.pcapng", "workspace/last_capture.pcapng"):
        p = Path(rel).resolve()
        debug_log(
            "verify_paths.py:main",
            "resolve relative",
            {"input": rel, "resolved": str(p), "exists": p.exists()},
            "D",
            run_id,
        )

    # H-E: analyze_pcapng actually works on known good path
    good = ROOT / "last_capture.pcapng"
    if good.exists():
        res = tools.analyze_pcapng(str(good), filter_expression="http", limit=3)
        debug_log(
            "verify_paths.py:main",
            "analyze root pcap",
            {"success": res.get("success"), "error": res.get("error", "")[:200]},
            "E",
            run_id,
        )

    print("Verification complete — see debug-d14d5b.log")


if __name__ == "__main__":
    main()
