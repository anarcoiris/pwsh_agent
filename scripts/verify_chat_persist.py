"""Simulate chat_turn stall scenario for PCAP goals."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.chat_goals import ChatGoals
from core.debug_log import debug_log

MSG = "locate last_capture.pcapng and analyze http packets for login, xml, xmlobj using tshark filters"


async def main():
    goals = ChatGoals.from_message(MSG)
    executed: list[str] = []

    # Simulate step 0: only append_note prose, goals still pending
    pending = goals.pending(executed)
    debug_log(
        "verify_chat_persist.py",
        "after append_note only",
        {"pending": pending, "would_nudge": bool(pending)},
        "F",
        "post-fix",
    )

    # Simulate bootstrap
    import tools
    ff = tools.find_file("last_capture.pcapng")
    path = ff.get("recommended") or "last_capture.pcapng"
    res = tools.analyze_pcapng(path, filter_expression=goals.filter_expression, limit=5)
    executed.extend(["find_file", "analyze_pcapng"])

    debug_log(
        "verify_chat_persist.py",
        "after bootstrap",
        {
            "pending": goals.pending(executed),
            "analyze_success": res.get("success"),
            "path": path,
        },
        "F",
        "post-fix",
    )
    print("Chat persist verify done — see debug-d14d5b.log")


if __name__ == "__main__":
    asyncio.run(main())
