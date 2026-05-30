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
        print(f"\n[🧠 Thought/Text]:\n{data}")
    elif event_type == "AGENT_TOOL_CALL":
        print(f"\n[⚒ Tool Call]: {data.get('tool')} with args {data.get('args')}")
    elif event_type == "AGENT_TOOL_RESULT":
        res = data.get("result", {})
        exit_code = res.get("exit_code") if isinstance(res, dict) else None
        print(f"[✔ Tool Result]: Exit Code: {exit_code}")

async def run():
    react_agent = agent.ReActAgent()
    prompt = (
        "Create a script named 'helloworld.py' (or a .ps1 script) in the local folder. "
        "The script must print this exact sentence: 'Hello World! This is a test from the simplified ReAct agent.' "
        "and then wait for the user to press Enter to finalize (e.g. input() in python or Read-Host in PowerShell). "
        "Use your write_file or host_exec tool to create the script, then verify it was written successfully."
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
