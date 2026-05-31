---
tools: [find_file, read_file, analyze_pcapng, run_script]
phase: [development, network]
---

# find_file Tool Playbook

## When to Use

**Use tool `find_file`** when the user names a file without a full path — PCAPs, scripts, configs, or deliverables anywhere in the project tree.

## Common Workflows

**PCAP analysis:**
```json
{"name": "find_file", "arguments": {"name": "last_capture.pcapng"}}
```
Then pass `recommended` from the result to `analyze_pcapng`.

**Locate Python deliverable:**
```json
{"name": "find_file", "arguments": {"name": "watcher.py"}}
```
Then `read_file` or `run_script` with the recommended path.

**Search before read:**
```json
{"name": "find_file", "arguments": {"name": "settings.json"}}
```

## Known Canonical Paths

| File | Search order |
|------|--------------|
| `last_capture.pcapng` | repo root → `workspace/` → `artifacts/captures/` |
| Scripts | project subdirs by name match |

**Does not exist:** `network_logs/last_capture.pcapng` — never invent this path.

## Example Response Usage

Tool returns `matches` (all paths) and `recommended` (best pick). Always prefer `recommended` for the next tool call.

## Do Not Use find_file For

- Content inspection → follow with **`read_file`** or **`analyze_pcapng`**
- Wildcard/glob search → pass exact basename
- Files outside project tree → tell user if no matches

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| Guessing paths after failed search | Report no matches; ask user for location |
| Skipping find_file for `last_capture.pcapng` | Always resolve path first |
| `host_exec Get-ChildItem -Recurse` | Use this tool |
