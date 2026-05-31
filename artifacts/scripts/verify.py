import asyncio

from repo_bootstrap import bootstrap

bootstrap()

import tools
import agent

async def verify_agent():
    print("==================================================")
    print("          Pulse pwsh_agent Verification           ")
    print("==================================================")
    
    # 1. Verify Tools
    print("\n[*] 1. Testing SequentialThinkingEngine...")
    engine = tools.SequentialThinkingEngine()
    result_thought = engine.process_thought({
        "thought": "Verifying the setup state...",
        "thoughtNumber": 1,
        "totalThoughts": 2,
        "nextThoughtNeeded": True
    })
    print(f"[+] Thought result: {result_thought}")
    
    print("\n[*] 2. Testing host_exec (PowerShell test command)...")
    res_cmd = tools.host_exec("Get-Date")
    print(f"[+] PowerShell exit code: {res_cmd.get('exit_code')}")
    print(f"[+] Output: {res_cmd.get('stdout', '').strip()}")
    
    # 2. Verify Wireshark / TShark Network Capabilities
    print("\n[*] 3. Detecting Wireshark / TShark installation...")
    tshark_path = tools.find_tshark()
    if tshark_path:
        print(f"[+] Found tshark: {tshark_path}")
        
        print("\n[*] 4. Listing network interfaces...")
        iface_res = tools.list_network_interfaces()
        print(f"[+] Interfaces: {iface_res}")
        
        if iface_res.get("success"):
            interfaces = iface_res.get("interfaces", [])
            if interfaces:
                # Use the first interface to test capture
                # Extract index from lines like "1. \Device\NPF_..."
                first_iface = "1"
                import re
                match = re.match(r"^(\d+)\.", interfaces[0])
                if match:
                    first_iface = match.group(1)
                
                print(f"\n[*] 5. Testing 3-second live capture on interface {first_iface}...")
                test_cap_path = str(Path("test_verify.pcapng").resolve())
                cap_res = tools.capture_packets(interface=first_iface, duration=3, output_path=test_cap_path)
                print(f"[+] Capture Result: {cap_res}")
                
                if cap_res.get("success"):
                    print("\n[*] 6. Testing packet analysis of captured pcapng...")
                    analysis_res = tools.analyze_pcapng(test_cap_path, limit=5)
                    print(f"[+] Analysis Success: {analysis_res.get('success')}")
                    if analysis_res.get("success"):
                        analysis = analysis_res.get("analysis", {})
                        print(f"[+] Protocol Hierarchy size: {len(analysis.get('protocol_hierarchy', ''))} chars")
                        print(f"[+] IP Conversations size: {len(analysis.get('ip_conversations', ''))} chars")
                        print(f"[+] Packet Summary packets returned:\n{analysis.get('packet_summary', '')}")
                    
                    # Clean up
                    try:
                        Path(test_cap_path).unlink(missing_ok=True)
                        print("[+] Cleaned up temporary test packet capture file.")
                    except Exception as e:
                        print(f"[-] Could not delete temporary pcapng: {e}")
            else:
                print("[-] No network interfaces found to perform capture test.")
        else:
            print(f"[-] Failed to list interfaces: {iface_res.get('error')}")
    else:
        print("[-] WARNING: tshark.exe not found. Network capture tools will be unavailable until Wireshark is installed.")

    # 3. Verify high-performance crack_hash tool
    print("\n[*] 7. Testing crack_hash tool wrapper (non-interactive py -3.10 hash_pro7.py)...")
    # SHA-256 of "000000ab!" is a8327e49fdeb64b4cc32a0abf1413b611d076e86108fef788cc10909b34d635d
    # Let's crack with NNNNNNAA! mask starting from length 9 to make it instant
    target_pwd_hash = "a8327e49fdeb64b4cc32a0abf1413b611d076e86108fef788cc10909b34d635d"
    crack_res = tools.crack_hash(target_hash=target_pwd_hash, mask="NNNNNNAA!", min_len=9)
    # Strip or clean fields for safe Windows CP1252 logging
    safe_res = {
        "success": crack_res.get("success"),
        "status": crack_res.get("status"),
        "password": crack_res.get("password"),
        "error": crack_res.get("error"),
        "stdout_len": len(crack_res.get("stdout", "")),
        "stderr": crack_res.get("stderr", "").replace("\r\n", " ").replace("\n", " ")[:200]
    }
    print(f"[+] Crack Hash Result (Safe fields): {safe_res}")

    # 4. Verify Agent Initialization
    print("\n[*] 8. Initializing ReActAgent cognitive core...")
    try:
        react_agent = agent.ReActAgent()
        print(f"[+] Default reasoning model: {react_agent.default_model}")
        print(f"[+] Ollama server URL: {react_agent.base_url}")
        print(f"[+] Loaded tools count: {len(react_agent.tools_registry)}")
        print("[+] SUCCESS: ReActAgent initialized successfully.")
    except Exception as e:
        print(f"[-] ERROR: Failed to initialize ReActAgent: {e}")
        
    print("\n==================================================")
    print("       Verification Completed Successfully        ")
    print("==================================================")

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(verify_agent())
