---
tools: [hash_identify]
phase: [exploit]
---

# hash_identify Tool Playbook

## Description
Identify the likely hash algorithm of a given hash string by pattern matching (MD5, SHA-1, SHA-256, bcrypt, Argon2, etc.).

## Example Invocation
```json
{
  "name": "hash_identify",
  "arguments": {
    "hash_value": "<hash_value>"
  }
}
```

## Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `hash_value` | `string` | **Yes** | The hash string to analyze. |

## Usage Notes
- Make sure to review the parameters carefully.
- Only call this tool when explicitly required by the task or when appropriate for the active exploit phase.
- Summarize the execution results back to the user in plain, concise markdown.
