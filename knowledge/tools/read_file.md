---
tools: [read_file]
phase: [development, general, network]
---

# read_file Tool Playbook

## When to Use

**Use tool `read_file`** to inspect existing files — source code, configs, tool output logs, and large PCAP verbose dumps written to `.pulse/pcap_logs/`.

## Common Workflows

**Verify deliverable before run:**
```json
{"name": "read_file", "arguments": {"path": "watcher/watcher.py"}}
```

**Chunked read of large verbose PCAP log:**
```json
{"name": "read_file", "arguments": {"path": ".pulse/pcap_logs/verbose_20250530.log", "line_start": 1, "line_count": 80}}
```

**Read next page of large file:**
```json
{"name": "read_file", "arguments": {"path": ".pulse/pcap_logs/verbose_20250530.log", "line_start": 81, "line_count": 80}}
```

**Inspect config before edit:**
```json
{"name": "read_file", "arguments": {"path": "config/settings.json", "line_start": 1, "line_count": 50}}
```

## Parameters

- **path** — Relative or absolute path (required).
- **line_start** — 1-based first line (default 1).
- **line_count** — Lines to read; omit for entire file (avoid on huge logs).

## After analyze_pcapng verbose=true

When `analyze_pcapng` writes a large decode to `.pulse/pcap_logs/`:

1. Note `verbose_log_file` from tool result
2. `read_file(path=…, line_start=1, line_count=80)` — first chunk
3. Increase `line_start` to paginate

## Do Not Use read_file For

- Writing or overwriting → use **`write_file`**
- Progress notes → use **`append_note`**
- Finding files by name → use **`find_file`** first

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| Reading entire multi-MB log | Use `line_start` + `line_count` chunks |
| Skipping read before `write_file` edit | Read first to avoid clobbering |
| `host_exec Get-Content` | Use this tool for structured line ranges |
