---
tools: [cve_lookup]
phase: [exploit, recon]
---

# cve_lookup Tool Playbook

## When to Use

**Use tool `cve_lookup`** after service/version discovery to find known vulnerabilities for a product, version string, or specific CVE ID.

## Vulnerability Research Workflow

| Step | Tool | Purpose |
|------|------|---------|
| 1 | `port_scan` / `http_headers_check` | Discover service + version (e.g. Server header) |
| 2 | `cve_lookup(keyword="Apache 2.4.49")` | Find related CVEs |
| 3 | `cve_lookup(keyword="CVE-2021-41773")` | Look up specific CVE |
| 4 | `finding_create(…)` | Record confirmed vulnerable version |

## Example Invocations

**Product search:**
```json
{"name": "cve_lookup", "arguments": {"keyword": "OpenSSL 3.0", "max_results": 5}}
```

**Specific CVE ID:**
```json
{"name": "cve_lookup", "arguments": {"keyword": "CVE-2024-1234", "max_results": 3}}
```

**Technology keyword:**
```json
{"name": "cve_lookup", "arguments": {"keyword": "Microsoft Exchange", "max_results": 10}}
```

## What It Returns

NIST NVD API results: CVE ID, published date, description snippet, CVSS v3 score, severity, vector string.

## Do Not Use cve_lookup For

- Live exploitation → research only; document with `finding_create`
- Version detection → use **`port_scan`**, **`http_headers_check`**, **`system_info`**
- Offline CVE databases → requires network to NVD API

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| Vague keyword `"apache"` | Include version from banner when known |
| Assuming exploitability | CVE listing ≠ confirmed vulnerable instance |
| `host_exec curl nvd` | Use this tool for structured JSON |
