---
tools: [list_network_interfaces]
phase: [network]
---

# list_network_interfaces Tool Playbook

## When to Use

**Use tool `list_network_interfaces`** as the first step before any live capture. It lists tshark-visible adapters with index numbers for `capture_packets`.

## Capture Workflow

| Step | Tool | Purpose |
|------|------|---------|
| 1 | `list_network_interfaces()` | Get interface index/name |
| 2 | `capture_packets(interface="1", duration=15, output_path="last_capture.pcapng")` | Live capture |
| 3 | `analyze_pcapng(file_path="last_capture.pcapng", filter_expression="http")` | Offline analysis |

## Example Invocation

```json
{"name": "list_network_interfaces", "arguments": {}}
```

No parameters. Returns adapter index, name, and description from tshark `-D` output.

## Choosing an Interface

- **Wi-Fi / Ethernet with traffic** — pick the adapter showing your active subnet.
- **Loopback (index 1 on some systems)** — only captures localhost; rarely useful for LAN tasks.
- **Multiple adapters** — capture on the one matching the target network from `system_info`.

## Do Not Use list_network_interfaces For

- Offline PCAP analysis → use **`analyze_pcapng`**
- Raw `tshark -D` via `host_exec` → use this tool unless it returns empty

## If tshark Is Missing

When the tool fails or returns no interfaces:

1. `find_tshark()` — locate binary (internal helper; may be exposed via error message)
2. Install Wireshark: `winget install WiresharkFoundation.Wireshark`
3. Retry `list_network_interfaces()`

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| Guessing interface `"1"` without listing | Always list first |
| Capturing on wrong adapter | Match adapter IP to target subnet from `system_info` |
| Skipping straight to `host_exec tshark -i` | Use `capture_packets` after listing |
