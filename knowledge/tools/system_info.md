---
tools: [system_info]
phase: [recon]
---

# system_info Tool Playbook

## When to Use

**Use tool `system_info`** at the start of local enumeration or when the user asks about the current machine's OS, network, or security posture.

## What It Returns

- OS caption, version, build, architecture, uptime, memory
- CPU name, cores, clock speed
- Active network adapters, MAC addresses, link speed
- IPv4 addresses and prefix lengths
- UAC status
- Windows Defender / antivirus state

## Example Invocation

```json
{"name": "system_info", "arguments": {}}
```

No parameters required.

## Typical Workflows

**Local recon baseline:**
```json
{"name": "system_info", "arguments": {}}
```
Then use adapter IPs to derive CIDR for `ping_sweep`.

**Pre-engagement host profile:**
1. `system_info()` — document OS and patch level
2. `list_network_interfaces()` — capture interfaces for packet work
3. `finding_create(…)` — record misconfigurations (UAC off, AV disabled)

## Follow-Up Tools

| Finding | Next tool |
|---------|-----------|
| Local IP `192.168.1.5/24` | `ping_sweep(cidr="192.168.1.0/24")` |
| Multiple adapters | `list_network_interfaces()` before capture |
| AV disabled | `finding_create(severity="HIGH", …)` |

## Do Not Use system_info For

- Remote target enumeration → use **`dns_lookup`**, **`port_scan`**
- Live packet capture → use **`capture_packets`**
- Raw `Get-CimInstance` via `host_exec` → use this tool

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| Re-running every turn | Call once per session unless hardware changed |
| Ignoring adapter list | Use IPs to scope `ping_sweep` correctly |
