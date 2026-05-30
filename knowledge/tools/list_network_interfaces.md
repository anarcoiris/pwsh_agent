---
tools: [list_network_interfaces]
phase: [network]
---

# list_network_interfaces Tool Playbook

## Description
Lists all available local network interfaces with tshark.

## Example Invocation
```json
{
  "name": "list_network_interfaces",
  "arguments": {}
}
```

## Usage Notes
- Make sure to review the parameters carefully.
- Only call this tool when explicitly required by the task or when appropriate for the active network phase.
- Summarize the execution results back to the user in plain, concise markdown.
