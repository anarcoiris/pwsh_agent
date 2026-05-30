"""Verify follow-up PCAP decode routing."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.chat_goals import ChatGoals, ChatGoalGuard
from core.debug_log import debug_log

FOLLOWUP = "can you decode the contents and look for the key values im looking for?"

messages = [
    {"role": "tool", "name": "find_file", "content": json.dumps({"success": True, "recommended": "last_capture.pcapng"})},
    {"role": "tool", "name": "analyze_pcapng", "content": json.dumps({"success": True, "analysis": {"packet_summary": "frame 1: http"}})},
]

goals = ChatGoals.from_session(messages, FOLLOWUP)
debug_log(
    "verify_followup.py",
    "session goals",
    {"goals": goals.label if goals else None, "verbose": goals.verbose if goals else None},
    "G",
    "post-fix",
)

_, _, enc_err = ChatGoalGuard.apply(
    "encode_decode", {"text": "x", "operation": "decode"}, goals, []
)
debug_log(
    "verify_followup.py",
    "encode_decode blocked",
    {"blocked": enc_err is not None, "err_head": (enc_err or "")[:80]},
    "G",
    "post-fix",
)

if goals:
    import tools
    res = tools.analyze_pcapng(
        goals.pcap_path_hint,
        filter_expression=goals.filter_expression,
        verbose=True,
        limit=5,
    )
    debug_log(
        "verify_followup.py",
        "verbose analyze",
        {"success": res.get("success"), "has_summary": bool(res.get("analysis", {}).get("packet_summary"))},
        "G",
        "post-fix",
    )

print("Follow-up verify done — see debug-d14d5b.log")
