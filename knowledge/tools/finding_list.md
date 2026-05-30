---
tools: [finding_list]
phase: [development]
---

# finding_list Tool Playbook

## Description
List persisted security findings from the local database, optionally filtered by severity.

## Example Invocation
```json
{
  "name": "finding_list",
  "arguments": {
    "severity_filter": "<severity_filter>",
    "limit": 10
  }
}
```

## Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `severity_filter` | `string` | No | Optional severity filter: CRITICAL | HIGH | MEDIUM | LOW | INFO. |
| `limit` | `integer` | No | Maximum number of findings to return (default: 50). |

## Usage Notes
- Make sure to review the parameters carefully.
- Only call this tool when explicitly required by the task or when appropriate for the active development phase.
- Summarize the execution results back to the user in plain, concise markdown.
