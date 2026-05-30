---
tools: [host_exec]
phase: [development, recon]
---

# host_exec Tool Playbook

## When to Use host_exec

**Use tool `host_exec`** for PowerShell one-liners and native Windows cmdlets. It is a **last resort** for tasks covered by specialized tools (`port_scan`, `analyze_pcapng`, `run_script`, etc.).

## PowerShell Scripts (.ps1)

```json
{"name": "host_exec", "arguments": {"command": "powershell -ExecutionPolicy Bypass -File script.ps1"}}
```

## Never Use host_exec For

- Running `.py` files → use **`run_script`**
- PCAP capture → use **`capture_packets`**
- PCAP analysis → use **`analyze_pcapng`**
- Port scanning → use **`port_scan`**

## Python via host_exec (fallback only)

If you must use host_exec for pip, the agent normalizes to venv python:

```json
{"name": "host_exec", "arguments": {"command": "python -m pip install watchdog"}}
```
