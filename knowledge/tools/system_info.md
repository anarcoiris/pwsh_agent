---
tools: [system_info]
phase: [recon]
---

# system_info Tool Playbook

## Description
Gather comprehensive local Windows system information: OS, CPU, network adapters, IP addresses, UAV status, antivirus state.

## Example Invocation
```json
{
  "name": "system_info",
  "arguments": {}
}
```

## Usage Notes
- Make sure to review the parameters carefully.
- Only call this tool when explicitly required by the task or when appropriate for the active recon phase.
- Summarize the execution results back to the user in plain, concise markdown.
