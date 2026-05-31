---
tools: [dns_lookup]
phase: [recon]
---

# dns_lookup Tool Playbook

## When to Use

**Use tool `dns_lookup`** as the first step when the user gives a hostname or domain. Resolve before `port_scan`, `ssl_analysis`, or `http_headers_check`.

## Recon Workflow

| Step | Tool | Purpose |
|------|------|---------|
| 1 | `dns_lookup(hostname=…, record_type="A")` | Resolve IP addresses |
| 2 | `dns_lookup(hostname=…, record_type="MX")` | Mail servers |
| 3 | `dns_lookup(hostname=…, record_type="TXT")` | SPF, DKIM, verification records |
| 4 | `port_scan(target=<resolved IP>)` | Scan services on resolved host |

## Example Invocations

**A record (default):**
```json
{"name": "dns_lookup", "arguments": {"hostname": "example.com"}}
```

**MX records:**
```json
{"name": "dns_lookup", "arguments": {"hostname": "example.com", "record_type": "MX"}}
```

**TXT / SPF:**
```json
{"name": "dns_lookup", "arguments": {"hostname": "example.com", "record_type": "TXT"}}
```

**Subdomain enumeration follow-up:**
```json
{"name": "dns_lookup", "arguments": {"hostname": "api.example.com", "record_type": "CNAME"}}
```

## Supported Record Types

`A`, `AAAA`, `MX`, `NS`, `TXT`, `CNAME`, `SOA` — pass via `record_type`.

## Do Not Use dns_lookup For

- Port scanning → use **`port_scan`**
- Live host discovery on a subnet → use **`ping_sweep`**
- HTTP header analysis → use **`http_headers_check`**

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| Scanning before resolving | Run `dns_lookup` first to get the IP |
| Using `host_exec nslookup` | Use the dedicated tool |
| Omitting record type for mail/SPF questions | Pass `record_type="MX"` or `"TXT"` |
