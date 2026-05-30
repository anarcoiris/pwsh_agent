---
tools: [read_file]
phase: [general]
---

# read_file Tool Playbook

## Description
Reads the text contents of a local file from the system. Supports reading files by line chunking for large files.

## Example Invocation
```json
{
  "name": "read_file",
  "arguments": {
    "path": "<path>",
    "line_start": 10,
    "line_count": 10
  }
}
```

## Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `path` | `string` | **Yes** | Absolute or relative path to the file. |
| `line_start` | `integer` | No | 1-based index of the first line to read (default: 1). |
| `line_count` | `integer` | No | Optional number of lines to read. If omitted, reads the entire file. |

## Usage Notes
- Make sure to review the parameters carefully.
- Only call this tool when explicitly required by the task or when appropriate for the active general phase.
- Summarize the execution results back to the user in plain, concise markdown.
