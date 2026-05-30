---
tools: [find_file, analyze_pcapng]
phase: [network]
---

# find_file Tool Playbook

## Locate PCAP and Deliverables

**Use tool `find_file`** when the user names a file without a full path (e.g. `last_capture.pcapng`).

```json
{"name": "find_file", "arguments": {"name": "last_capture.pcapng"}}
```

Then pass `recommended` from the result to `analyze_pcapng`.

## Known Paths After Repo Cleanup

- `last_capture.pcapng` — project root (primary), also `workspace/`, `artifacts/captures/`
- **Does not exist:** `network_logs/last_capture.pcapng`

Never invent directories. If `find_file` returns no matches, tell the user — do not hallucinate paths.
