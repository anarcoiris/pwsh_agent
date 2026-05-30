---
tools: [encode_decode]
phase: [exploit]
---

# encode_decode Tool Playbook

## Description
Encode or decode text using common schemes: base64, base64url, hex, url, rot13, utf8_bytes.

## Example Invocation
```json
{
  "name": "encode_decode",
  "arguments": {
    "text": "<text>",
    "operation": "<operation>",
    "encoding": "<encoding>"
  }
}
```

## Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `text` | `string` | **Yes** | Input text to process. |
| `operation` | `string` | **Yes** | 'encode' or 'decode'. |
| `encoding` | `string` | No | Scheme: base64 | base64url | hex | url | rot13 | utf8_bytes (default: base64). |

## Usage Notes
- Make sure to review the parameters carefully.
- Only call this tool when explicitly required by the task or when appropriate for the active exploit phase.
- Summarize the execution results back to the user in plain, concise markdown.
