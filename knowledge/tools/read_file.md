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

- **path** — Relative or absolute path (required). Globs like `report_*.md` or `verbose_*.txt` are expanded via find_file ranking (same as grep_file).
- **line_start** — 1-based first line (default 1).
- **line_count** — Lines to read; omit for entire file (avoid on huge logs).

## Resolving paths

Prefer explicit paths when available:

1. `find_file('report_*.md').recommended` or `verbose_log_file` from analyze_pcapng / SESSION FACTS
2. Globs work directly: `read_file(path='report_*.md')` or `read_file(path='.pulse/pcap_logs/verbose_*.txt', line_count=80)`

## After analyze_pcapng verbose=true

When `analyze_pcapng` writes a large decode to `.pulse/pcap_logs/`:

1. Note `verbose_log_file` from tool result
2. `read_file(path=…, line_start=1, line_count=80)` — first chunk
3. Increase `line_start` to paginate

## Do Not Use read_file For

- Writing or overwriting → use **`write_file`**
- Progress notes → use **`append_note`**
- Finding files by name → use **`find_file`** when path is unknown; globs also work on path directly

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| Reading entire multi-MB log | Use `line_start` + `line_count` chunks |
| Skipping read before `write_file` edit | Read first to avoid clobbering |
| `host_exec Get-Content` | Use this tool for structured line ranges |
| Guessing report/log paths | Use find_file or glob path; check SESSION FACTS |
