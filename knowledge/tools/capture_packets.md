---
tools: [capture_packets]
phase: [network]
---

# capture_packets Tool Playbook

## When to Use

**Use tool `capture_packets`** for live traffic collection. Always list interfaces first, then capture with a bounded duration.

## Staged Capture Workflow

| Step | Tool | Purpose |
|------|------|---------|
| 1 | `list_network_interfaces()` | Pick correct adapter index |
| 2 | `capture_packets(interface="2", duration=15, output_path="last_capture.pcapng")` | Record traffic |
| 3 | `find_file(name="last_capture.pcapng")` | Confirm saved path |
| 4 | `analyze_pcapng(file_path=…, filter_expression="http", limit=30)` | Index protocols |

## Example Invocations

**Default 10-second capture:**
```json
{"name": "capture_packets", "arguments": {"interface": "1", "duration": 10}}
```

**Named output for later analysis:**
```json
{"name": "capture_packets", "arguments": {
  "interface": "2",
  "duration": 20,
  "output_path": "last_capture.pcapng"
}}
```

**Longer web-traffic sample:**
```json
{"name": "capture_packets", "arguments": {
  "interface": "2",
  "duration": 30,
  "output_path": "artifacts/captures/web_session.pcapng"
}}
```

## Parameters

- **interface** — Index or name from `list_network_interfaces` (default `"1"`).
- **duration** — Seconds to capture (default 10). Keep bounded to avoid huge files.
- **output_path** — Save location (default `capture.pcapng` in workspace). Prefer `last_capture.pcapng` at repo root for consistency with `find_file`.

## Do Not Use capture_packets For

- Offline PCAP already on disk → use **`analyze_pcapng`**
- Display filtering during capture → capture raw, filter in `analyze_pcapng`
- Raw `tshark -i … -w …` via `host_exec` → use this tool

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| No `list_network_interfaces` first | List adapters before capture |
| Unbounded capture | Always set `duration` (10–30s typical) |
| Wrong output path | Use `last_capture.pcapng` or `find_file` after capture |
| Analyzing before user generates traffic | Tell user to browse/login during capture window |
