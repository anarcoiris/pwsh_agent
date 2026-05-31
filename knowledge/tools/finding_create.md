---
tools: [finding_create, finding_list, report_generate]
phase: [development, recon, network, exploit]
---

# finding_create Tool Playbook

## When to Use

**Use tool `finding_create`** to persist security observations during an engagement — after recon, web checks, PCAP analysis, or hash tasks.

## Engagement Documentation Workflow

| Step | Tool | Purpose |
|------|------|---------|
| 1 | Recon/analysis tools | Gather evidence |
| 2 | `finding_create(…)` | Record each significant issue |
| 3 | `finding_list()` | Review accumulated findings |
| 4 | `report_generate()` | Export Markdown report to `output/` |

## Example Invocations

**Missing security headers (from http_headers_check):**
```json
{"name": "finding_create", "arguments": {
  "title": "Missing HSTS and CSP headers",
  "severity": "MEDIUM",
  "description": "HTTPS site lacks Strict-Transport-Security and Content-Security-Policy.",
  "target": "https://example.com",
  "evidence": "Server: nginx/1.18; no HSTS in response headers",
  "recommendation": "Add HSTS with max-age >= 31536000 and define CSP."
}}
```

**Open RDP port (from port_scan):**
```json
{"name": "finding_create", "arguments": {
  "title": "RDP exposed on LAN host",
  "severity": "HIGH",
  "description": "TCP 3389 open on internal host.",
  "target": "192.168.1.10",
  "evidence": "port_scan open_ports: [{\"Port\": \"3389\", \"State\": \"open\"}]"
}}
```

**Plaintext credentials in PCAP:**
```json
{"name": "finding_create", "arguments": {
  "title": "HTTP Basic Auth credentials in cleartext",
  "severity": "CRITICAL",
  "description": "Login credentials observed in unencrypted HTTP traffic.",
  "target": "last_capture.pcapng",
  "evidence": "analyze_pcapng potential_plaintext_credentials entry",
  "recommendation": "Enforce HTTPS and rotate exposed credentials."
}}
```

## Severity Levels

`CRITICAL` | `HIGH` | `MEDIUM` | `LOW` | `INFO` — must be uppercase.

## Parameters

- **title**, **severity**, **description** — required.
- **target**, **evidence**, **recommendation**, **specialist** — optional but recommended.

## Do Not Use finding_create For

- User-facing chat summaries → respond in markdown to user
- Code deliverables → use **`write_file`**
- Session progress → use **`append_note`**

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| Lowercase severity `"high"` | Use `"HIGH"` |
| Empty evidence field | Paste relevant tool output snippet |
| Creating report before findings | Call `finding_create` at least once first |
