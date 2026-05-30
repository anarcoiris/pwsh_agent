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
        print(f"[Tool Result]: {res}")

async def run():
    react_agent = agent.ReActAgent()
    prompt = (
        "Identify the network interfaces, capture packets on the loopback interface (interface indices list from list_network_interfaces) "
        "for 3 seconds, and then run a full packet analysis on the capture."
    )
    print("[*] Starting mission execution...")
    result = await react_agent.run_mission(prompt, step_callback=callback)
    print("\n==================================================")
    print("[+] Agent's Final Response:")
    print(result)
    print("==================================================")

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(run())
