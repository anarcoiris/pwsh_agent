import asyncio
import sys
from pathlib import Path

# Ensure local imports work
sys.path.append(str(Path(__file__).resolve().parent))

import agent

def callback(event_type, data):
    if event_type == "AGENT_STATUS":
        print(f"\n[*] Status: {data}")
    elif event_type == "AGENT_TEXT":
        print(f"\n[Thought/Text]:\n{data}")
    elif event_type == "AGENT_TOOL_CALL":
        print(f"\n[Tool Call]: {data.get('tool')} with args {data.get('args')}")
    elif event_type == "AGENT_TOOL_RESULT":
        res = data.get("result", {})
        # Strip heavy stdout logs for clean console printing
        if isinstance(res, dict) and "stdout" in res:
            res = {
                "success": res.get("success"),
                "status": res.get("status"),
                "password": res.get("password"),
                "error": res.get("error")
            }
        print(f"[Tool Result]: {res}")

async def run():
    react_agent = agent.ReActAgent()
    prompt = (
        "Crack the SHA-256 target hash 'a8327e49fdeb64b4cc32a0abf1413b611d076e86108fef788cc10909b34d635d' "
        "using our high-performance 'crack_hash' tool. The password matches mask 'NNNNNNAA!' "
        "and is known to be exactly 9 characters long (so start cracking at min_len 9). "
        "Summarize the result and declare MISSION_COMPLETE."
    )
    print("[*] Starting ReAct hash cracking mission...")
    result = await react_agent.run_mission(prompt, step_callback=callback)
    print("\n==================================================")
    print("[+] Agent's Final Response:")
    print(result)
    print("==================================================")

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(run())
