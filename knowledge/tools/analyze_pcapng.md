---
tools: [analyze_pcapng]
phase: [network]
---

# analyze_pcapng Tool Playbook

## Description
Analyzes an existing pcapng/pcap file using tshark to yield protocol stats, network conversations, potential plaintext passwords, and custom filtered logs. Supports verbose packet decodes and hex byte dumps.

## Example Invocation
```json
{
  "name": "analyze_pcapng",
  "arguments": {
    "file_path": "<file_path>",
    "filter_expression": "<filter_expression>",
    "limit": 10,
    "verbose": false,
    "show_bytes": false
  }
}
```

## Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `file_path` | `string` | **Yes** | Absolute or relative path to the pcapng file. |
| `filter_expression` | `string` | No | Optional Wireshark/tshark display filter expression (e.g. 'frame.number == 8' or 'http'). |
| `limit` | `integer` | No | Maximum number of packet summary lines to return. Defaults to 50. |
| `verbose` | `boolean` | No | If true, returns a full verbose protocol decode for matching packets (tshark -V). Defaults to false. |
| `show_bytes` | `boolean` | No | If true, returns a hex/ASCII dump of raw packet payload bytes (tshark -x). Defaults to false. |

## Usage Notes
- Make sure to review the parameters carefully.
- Only call this tool when explicitly required by the task or when appropriate for the active network phase.
- Summarize the execution results back to the user in plain, concise markdown.
