---
tools: [grep_file, read_file]
phase: [development, network]
---

# grep_file

## When to Use
- Search verbose logs and large artifacts for specific indicators (`xmlObj`, `Password,Username`, hashes).
- Retrieve targeted matches with context lines before deeper `read_file` chunking.

## Typical Workflow
1. `find_file('verbose_*.txt')` or use `verbose_log_file` from analyze_pcapng / SESSION FACTS.
2. `grep_file(path=<recommended_or_glob>, pattern=..., context_lines=2)` — globs like `.pulse/pcap_logs/verbose_*.txt` resolve via find_file ranking.
3. `read_file(path=..., line_start=..., line_count=...)` — inspect nearby full blocks if needed.

## Example
```json
{"name":"grep_file","arguments":{"path":".pulse/pcap_logs/verbose_20260531_223711.txt","pattern":"xmlObj|Password,Username|[a-fA-F0-9]{64}","max_matches":40,"context_lines":1,"case_insensitive":true}}
```

## Do Not Use
- Do not use for directory traversal (use `find_file` first).
- Do not use broad `.*` patterns that return the whole file; prefer specific keywords.
