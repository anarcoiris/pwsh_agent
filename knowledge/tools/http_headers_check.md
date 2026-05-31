---
tools: [http_headers_check]
phase: [recon, network]
---

# http_headers_check Tool Playbook

## When to Use

**Use tool `http_headers_check`** after `port_scan` shows 80/443/8080 open, or when the user asks about web server headers or security posture.

## Web Enumeration Workflow

| Step | Tool | Purpose |
|------|------|---------|
| 1 | `port_scan(target=…, ports="80,443,8080")` | Confirm web ports open |
| 2 | `http_headers_check(url="https://target/")` | Fetch headers + security notes |
| 3 | `ssl_analysis(hostname=…)` | Certificate details if HTTPS |
| 4 | `finding_create(…)` | Record missing security headers |

## Example Invocations

**HTTPS homepage:**
```json
{"name": "http_headers_check", "arguments": {"url": "https://example.com"}}
```

**HTTP on alternate port:**
```json
{"name": "http_headers_check", "arguments": {"url": "http://192.168.1.10:8080/"}}
```

**API endpoint:**
```json
{"name": "http_headers_check", "arguments": {"url": "https://api.example.com/v1/health"}}
```

## What It Checks

Returns response status, all headers, and flags:

- Missing `Strict-Transport-Security`, `X-Content-Type-Options`, `X-Frame-Options`
- Missing `Content-Security-Policy`, `Referrer-Policy`, `Permissions-Policy`
- Exposed `Server` fingerprint

## Do Not Use http_headers_check For

- TLS certificate expiry / cipher details → use **`ssl_analysis`**
- PCAP HTTP bodies → use **`analyze_pcapng`**
- Full page content / crawling → HEAD request only; note limitation

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| URL without scheme | Include `http://` or `https://` |
| `host_exec Invoke-WebRequest` | Use this tool for structured security notes |
| Checking cert via headers tool | Use `ssl_analysis` for TLS cert |
