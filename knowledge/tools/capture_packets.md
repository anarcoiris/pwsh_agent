---
tools: [capture_packets]
phase: [network]
---

# capture_packets Tool Playbook

## Description
Captures live packets on a specified network interface for a set duration.

## Example Invocation
```json
{
  "name": "capture_packets",
  "arguments": {
    "interface": "<interface>",
    "duration": 10,
    "output_path": "<output_path>"
  }
}
```

## Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `interface` | `string` | No | Interface index or name (from list_network_interfaces). Defaults to '1'. |
| `duration` | `integer` | No | Duration in seconds to capture packet traffic. Defaults to 10. |
| `output_path` | `string` | No | Target path to save the .pcapng file. Defaults to 'capture.pcapng' in workspace. |

## Usage Notes
- Make sure to review the parameters carefully.
- Only call this tool when explicitly required by the task or when appropriate for the active network phase.
- Summarize the execution results back to the user in plain, concise markdown.
