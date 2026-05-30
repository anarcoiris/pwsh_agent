---
tools: [finding_create]
phase: [development]
---

# finding_create Tool Playbook

## Description
Create and persist a security finding to the local SQLite database.

## Example Invocation
```json
{
  "name": "finding_create",
  "arguments": {
    "title": "<title>",
    "severity": "<severity>",
    "description": "<description>",
    "target": "<target>",
    "evidence": "<evidence>",
    "recommendation": "<recommendation>",
    "specialist": "<specialist>"
  }
}
```

## Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `title` | `string` | **Yes** | Short descriptive title of the finding. |
| `severity` | `string` | **Yes** | Severity: CRITICAL | HIGH | MEDIUM | LOW | INFO. |
| `description` | `string` | **Yes** | Detailed description of what was found. |
| `target` | `string` | No | Affected host, URL, or file path (optional). |
| `evidence` | `string` | No | Raw evidence snippet (output, log, etc.) (optional). |
| `recommendation` | `string` | No | Suggested remediation steps (optional). |
| `specialist` | `string` | No | Active specialist mode at time of finding (default: lead). |

## Usage Notes
- Make sure to review the parameters carefully.
- Only call this tool when explicitly required by the task or when appropriate for the active development phase.
- Summarize the execution results back to the user in plain, concise markdown.
