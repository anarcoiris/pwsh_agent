---
tools: [dns_lookup]
phase: [recon]
---

# dns_lookup Tool Playbook

## Description
Resolve DNS records for a hostname using native PowerShell Resolve-DnsName.

## Example Invocation
```json
{
  "name": "dns_lookup",
  "arguments": {
    "hostname": "<hostname>",
    "record_type": "<record_type>"
  }
}
```

## Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `hostname` | `string` | **Yes** | The target hostname or domain to resolve. |
| `record_type` | `string` | No | DNS record type — A, AAAA, MX, NS, TXT, CNAME, SOA (default: A). |

## Usage Notes
- Make sure to review the parameters carefully.
- Only call this tool when explicitly required by the task or when appropriate for the active recon phase.
- Summarize the execution results back to the user in plain, concise markdown.
