---
tools: [cve_lookup]
phase: [exploit]
---

# cve_lookup Tool Playbook

## Description
Look up recent CVEs from the NIST NVD API by keyword or CVE ID. Returns severity, CVSS score, and descriptions.

## Example Invocation
```json
{
  "name": "cve_lookup",
  "arguments": {
    "keyword": "<keyword>",
    "max_results": 10
  }
}
```

## Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `keyword` | `string` | **Yes** | Search keyword (product name, CVE ID, or technology). |
| `max_results` | `integer` | No | Maximum number of results to return (default: 5). |

## Usage Notes
- Make sure to review the parameters carefully.
- Only call this tool when explicitly required by the task or when appropriate for the active exploit phase.
- Summarize the execution results back to the user in plain, concise markdown.
