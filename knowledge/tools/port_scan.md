---
tools: [port_scan]
phase: [recon]
---

# port_scan Tool Playbook

## When to Use

**Use tool `port_scan`** after host discovery (`ping_sweep`) or DNS resolution (`dns_lookup`) to identify open TCP services.

## Recon Workflow

| Step | Tool | Purpose |
|------|------|---------|
| 1 | `dns_lookup` or `ping_sweep` | Identify target IP |
| 2 | `port_scan(target=…, ports="22,80,443,445,3389")` | Quick common-port scan |
| 3 | `http_headers_check(url=…)` | If 80/443 open |
| 4 | `ssl_analysis(hostname=…)` | If 443 open |
| 5 | `finding_create(…)` | Record exposed services |

## Port Specification Modes

| Mode | Example | Backend |
|------|---------|---------|
| Comma list | `"22,80,443,8080"` | Native `Test-NetConnection` |
| Range | `"1-1024"` | Requires **nmap** (`winget install Insecure.Nmap`) |
| Default | omit `ports` | Common ports: 22, 80, 443, 445, 3389, 8080, 8443 |

## Example Invocations

**Common ports on resolved host:**
```json
{"name": "port_scan", "arguments": {"target": "192.168.1.10", "ports": "22,80,443,445,3389", "timeout_ms": 1000}}
```

**Web-focused scan:**
```json
{"name": "port_scan", "arguments": {"target": "example.com", "ports": "80,443,8080,8443"}}
```

**Full range (needs nmap):**
```json
{"name": "port_scan", "arguments": {"target": "192.168.1.10", "ports": "1-1024", "timeout_ms": 2000}}
```

## Parameters

- **target** — IP or hostname (required).
- **ports** — Comma list or hyphen range.
- **timeout_ms** — Per-port connect timeout (default 1000).

## Do Not Use port_scan For

- Subnet host discovery → use **`ping_sweep`**
- UDP scanning → not supported; note limitation to user
- Raw `Test-NetConnection` via `host_exec` → use this tool

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| Range scan without nmap | Install nmap or use comma-separated port list |
| Scanning before DNS resolve | Run `dns_lookup` when given a hostname |
| `host_exec nmap` for a few ports | Use `port_scan` for structured JSON output |
