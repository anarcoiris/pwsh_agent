---
tools: [ping_sweep]
phase: [recon]
---

# ping_sweep Tool Playbook

## When to Use

**Use tool `ping_sweep`** to discover live hosts on a local subnet before targeted `port_scan` or service enumeration.

## Recon Workflow

| Step | Tool | Purpose |
|------|------|---------|
| 1 | `system_info()` | Identify local IP ranges and adapters |
| 2 | `ping_sweep(cidr="192.168.1.0/24")` | Find live hosts |
| 3 | `port_scan(target=<live IP>, ports="22,80,443,445,3389")` | Scan each live host |

## CIDR and Range Formats

| Input | Meaning |
|-------|---------|
| `192.168.1.0/24` | Full /24 sweep (hosts .1–.254) |
| `192.168.1.1-50` | Hosts 192.168.1.1 through 192.168.1.50 |
| `10.0.0.5` | Single host ping |

## Example Invocations

**Full /24 sweep:**
```json
{"name": "ping_sweep", "arguments": {"cidr": "192.168.1.0/24", "timeout_ms": 500}}
```

**Targeted range:**
```json
{"name": "ping_sweep", "arguments": {"cidr": "192.168.1.1-50", "timeout_ms": 300}}
```

**Quick single-host check:**
```json
{"name": "ping_sweep", "arguments": {"cidr": "10.0.0.1"}}
```

## Parameters

- **cidr** — CIDR notation or IP range (required).
- **timeout_ms** — Ping timeout per host (default 500). Lower for faster sweeps on trusted LANs.

## Do Not Use ping_sweep For

- DNS resolution → use **`dns_lookup`**
- TCP port state → use **`port_scan`**
- Internet-wide scanning → keep scope to authorized subnets only

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| `host_exec Test-Connection` loop | Use `ping_sweep` — parallel and structured |
| Wrong CIDR format | Use `x.x.x.0/24` or `x.x.x.1-50` |
| Skipping port scan on live hosts | Follow sweep with `port_scan` on each live IP |
