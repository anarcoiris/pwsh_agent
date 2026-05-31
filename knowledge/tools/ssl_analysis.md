---
tools: [ssl_analysis]
phase: [recon, network]
---

# ssl_analysis Tool Playbook

## When to Use

**Use tool `ssl_analysis`** when HTTPS is detected (`port_scan` port 443 open) or the user asks about certificates, TLS version, or cipher strength.

## Web/TLS Workflow

| Step | Tool | Purpose |
|------|------|---------|
| 1 | `dns_lookup(hostname=…)` | Resolve host |
| 2 | `port_scan(target=…, ports="443")` | Confirm TLS port |
| 3 | `ssl_analysis(hostname=…, port=443)` | Cert, cipher, TLS version |
| 4 | `finding_create(…)` | Record weak TLS or expired cert |

## Example Invocations

**Standard HTTPS (port 443 default):**
```json
{"name": "ssl_analysis", "arguments": {"hostname": "example.com"}}
```

**Explicit port:**
```json
{"name": "ssl_analysis", "arguments": {"hostname": "mail.example.com", "port": 443}}
```

**TLS on alternate port:**
```json
{"name": "ssl_analysis", "arguments": {"hostname": "192.168.1.10", "port": 8443}}
```

## What It Returns

- TLS version (flags TLSv1 / TLSv1.1 as weak)
- Cipher suite (flags RC4, DES, NULL)
- Certificate subject, issuer, expiry (`not_after`)
- Subject Alternative Names (SANs)
- Security notes array

## Do Not Use ssl_analysis For

- HTTP response headers → use **`http_headers_check`**
- Port 80 plain HTTP → no TLS to analyze
- Certificate pinning bypass / MITM → out of scope

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| `port: 80` for HTTPS | Default is 443; only set port for non-standard TLS |
| Analyzing IP before DNS | Hostname preferred for SNI; IP works but cert may mismatch |
| `host_exec openssl s_client` | Use this tool for structured output |
