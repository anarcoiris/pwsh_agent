---
tools: [sequentialthinking]
phase: [general]
---

# sequentialthinking Tool Playbook

## Description
A stateful tool for dynamic and reflective problem-solving through thoughts.

## Example Invocation
```json
{
  "name": "sequentialthinking",
  "arguments": {
    "thought": "<thought>",
    "nextThoughtNeeded": false,
    "thoughtNumber": 10,
    "totalThoughts": 10,
    "isRevision": false,
    "revisesThought": 10
  }
}
```

## Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `thought` | `string` | **Yes** | Your current detailed thinking step. |
| `nextThoughtNeeded` | `boolean` | **Yes** | Whether another sequential thought step is needed. |
| `thoughtNumber` | `integer` | **Yes** | Current thought step index (1-based). |
| `totalThoughts` | `integer` | **Yes** | Current estimate of total thought steps needed. |
| `isRevision` | `boolean` | No | True if this thought revises a previous thinking step. |
| `revisesThought` | `integer` | No | The thought index that is being revised. |

## Usage Notes
- Make sure to review the parameters carefully.
- Only call this tool when explicitly required by the task or when appropriate for the active general phase.
- Summarize the execution results back to the user in plain, concise markdown.
