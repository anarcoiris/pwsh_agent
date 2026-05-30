---
tools: [http_headers_check]
phase: [network]
---

# http_headers_check Tool Playbook

## Description
Fetch HTTP response headers and analyze security posture of a web endpoint (missing headers, server fingerprint, etc.).

## Example Invocation
```json
{
  "name": "http_headers_check",
  "arguments": {
    "url": "<url>"
  }
}
```

## Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `url` | `string` | **Yes** | Full URL to inspect (e.g., https://example.com). |

## Usage Notes
- Make sure to review the parameters carefully.
- Only call this tool when explicitly required by the task or when appropriate for the active network phase.
- Summarize the execution results back to the user in plain, concise markdown.
