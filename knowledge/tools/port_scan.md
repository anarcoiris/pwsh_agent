---
tools: [port_scan]
phase: [recon]
---

# port_scan Tool Playbook

## Description
Scan TCP ports on a target using native PowerShell Test-NetConnection. Falls back to nmap for port ranges.

## Example Invocation
```json
{
  "name": "port_scan",
  "arguments": {
    "target": "<target>",
    "ports": "<ports>",
    "timeout_ms": 10
  }
}
```

## Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `target` | `string` | **Yes** | IP address or hostname to scan. |
| `ports` | `string` | No | Comma-separated port list (e.g., '22,80,443') or range '1-1024'. Default: common ports. |
| `timeout_ms` | `integer` | No | Connection timeout in milliseconds (default: 1000). |

## Usage Notes
- Make sure to review the parameters carefully.
- Only call this tool when explicitly required by the task or when appropriate for the active recon phase.
- Summarize the execution results back to the user in plain, concise markdown.
