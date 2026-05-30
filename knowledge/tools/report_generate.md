---
tools: [report_generate]
phase: [development]
---

# report_generate Tool Playbook

## Description
Generate a structured Markdown engagement report from all findings in the local database, sorted by severity.

## Example Invocation
```json
{
  "name": "report_generate",
  "arguments": {
    "output_format": "<output_format>",
    "title": "<title>"
  }
}
```

## Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `output_format` | `string` | No | Output format: markdown | text (default: markdown). |
| `title` | `string` | No | Report title (default: 'Pulse Agent Engagement Report'). |

## Usage Notes
- Make sure to review the parameters carefully.
- Only call this tool when explicitly required by the task or when appropriate for the active development phase.
- Summarize the execution results back to the user in plain, concise markdown.
