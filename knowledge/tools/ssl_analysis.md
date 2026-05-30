---
tools: [ssl_analysis]
phase: [network]
---

# ssl_analysis Tool Playbook

## Description
Analyze the SSL/TLS certificate and configuration of a remote host (version, cipher, expiry, SANs, weak protocols).

## Example Invocation
```json
{
  "name": "ssl_analysis",
  "arguments": {
    "hostname": "<hostname>",
    "port": 80
  }
}
```

## Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `hostname` | `string` | **Yes** | Target hostname to connect to. |
| `port` | `integer` | No | TLS port (default: 443). |

## Usage Notes
- Make sure to review the parameters carefully.
- Only call this tool when explicitly required by the task or when appropriate for the active network phase.
- Summarize the execution results back to the user in plain, concise markdown.
