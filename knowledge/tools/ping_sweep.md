---
tools: [ping_sweep]
phase: [recon]
---

# ping_sweep Tool Playbook

## Description
Discover live hosts in a subnet using parallel PowerShell ping sweep.

## Example Invocation
```json
{
  "name": "ping_sweep",
  "arguments": {
    "cidr": "<cidr>",
    "timeout_ms": 10
  }
}
```

## Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `cidr` | `string` | **Yes** | Target network in CIDR notation (e.g., 192.168.1.0/24) or IP range like 192.168.1.1-50. |
| `timeout_ms` | `integer` | No | Ping timeout in milliseconds (default: 500). |

## Usage Notes
- Make sure to review the parameters carefully.
- Only call this tool when explicitly required by the task or when appropriate for the active recon phase.
- Summarize the execution results back to the user in plain, concise markdown.
