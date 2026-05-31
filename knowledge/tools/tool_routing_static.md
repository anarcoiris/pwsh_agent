---
phase: [general, development, recon, network, exploit]
tools: [host_exec, run_script, read_file, write_file, append_note, find_file, list_network_interfaces, capture_packets, analyze_pcapng, find_tshark, crack_hash, dns_lookup, ping_sweep, port_scan, http_headers_check, ssl_analysis, cve_lookup, system_info, encode_decode, hash_identify, finding_create, finding_list, report_generate, sequentialthinking]
---

# Tool Routing Static Reference

Use this as a compact, always-applicable "when to use" map across all tools.

## Development and File Operations

### `write_file`
- Use for code or document deliverables that must exist on disk.
- Do not use for progress/status logs.
- Pair with `read_file` before edits when replacing existing content.

### `run_script`
- Preferred for running `.py` scripts and tests in project venv.
- Do not run Python through `host_exec` unless absolutely necessary.
- Pair with `host_exec` only for dependency install or environment fixes.

### `read_file`
- Use for inspecting code, config, logs, and chunked reads of large files.
- Use `line_start`/`line_count` for pagination on large outputs.
- Do not use for writes.

### `append_note`
- Use only for timestamped progress lines in workspace note files.
- Do not use for code or full-file overwrites.

### `host_exec`
- Use for PowerShell one-liners and native host commands.
- Last resort when no specialized tool exists.
- Do not use for workflows covered by dedicated tools.

## Discovery and Resolution

### `find_file`
- Use when user references a filename without full path.
- Prefer its `recommended` path in follow-up tool calls.
- Do not guess directories if not found.

### `dns_lookup`
- First step for host/domain tasks before scanning or TLS checks.
- Use `record_type` for A/AAAA/MX/NS/TXT/CNAME/SOA.
- Do not use for port scanning.

### `ping_sweep`
- Use for subnet live-host discovery.
- Run before targeted `port_scan`.
- Do not use for DNS resolution.

### `port_scan`
- Use for TCP service discovery on known targets.
- Comma list uses native checks; ranges may require nmap.
- Do not use for subnet host discovery.

### `system_info`
- Use for local host baseline (OS/CPU/adapters/IP/UAC/AV).
- Good first step in local recon.
- Do not use for remote target scanning.

## Network Capture and Traffic Analysis

### `list_network_interfaces`
- Use before every live capture.
- Select interface index/name based on active adapter.

### `capture_packets`
- Use for bounded-duration live traffic capture.
- Capture first, then analyze offline.
- Avoid unbounded captures.

### `analyze_pcapng`
- Use for staged PCAP analysis (broad filter -> narrow filter -> verbose decode).
- Prefer display filters and capped limits.
- Read verbose log in chunks via `read_file` when generated.

### `find_tshark`
- Use when tshark-dependent tools fail due to missing binary.
- Resolve/install tshark, then retry specialized packet tools.

### `http_headers_check`
- Use for web header security posture checks on HTTP/HTTPS endpoints.
- Follow `port_scan` open web ports.
- Do not use for TLS certificate internals.

### `ssl_analysis`
- Use for TLS cert/version/cipher analysis on HTTPS services.
- Follow DNS/port validation.
- Do not use for HTTP header checks.

## Encoding, Hashing, and Vulnerability Intel

### `encode_decode`
- Use for text transformations (base64/base64url/hex/url/rot13/utf8_bytes).
- Useful for payload and artifact decoding.
- Do not use on full binary/pcap files.

### `hash_identify`
- Use to classify unknown hash format before cracking strategy.
- Follow with `crack_hash` when suitable.

### `crack_hash`
- Use for planned hash cracking with mask/salt/prefix/suffix strategy.
- Explicitly pass salt when user provides one.
- Do not replace with ad-hoc `host_exec` cracking commands.

### `cve_lookup`
- Use for vulnerability research by keyword/version/CVE ID.
- Pair with observed version data from recon tools.
- Research output is not proof of exploitability.

## Findings and Reporting

### `finding_create`
- Use to persist confirmed observations/evidence during assessment.
- Include severity, target, evidence, and recommendation where possible.

### `finding_list`
- Use to review/filter persisted findings prior to reporting.

### `report_generate`
- Use at end of workflow to produce structured markdown report.
- Requires existing findings in DB.

## Reasoning Support

### `sequentialthinking`
- Use for short planning/reflection steps before complex multi-tool actions.
- Plan, then execute real tools.
- Do not substitute for tool execution.

## Quick Routing Rules

- Need to create/modify deliverable file -> `write_file`
- Need to run Python -> `run_script`
- Need to inspect existing file/log -> `read_file`
- Need quick host command not covered elsewhere -> `host_exec`
- Need filename resolution -> `find_file`
- Need host resolution -> `dns_lookup`
- Need live hosts in subnet -> `ping_sweep`
- Need open TCP ports -> `port_scan`
- Need local machine baseline -> `system_info`
- Need packet interfaces -> `list_network_interfaces`
- Need packet capture -> `capture_packets`
- Need pcap analysis -> `analyze_pcapng`
- tshark missing/broken -> `find_tshark`
- Need HTTP header posture -> `http_headers_check`
- Need TLS cert/version/cipher -> `ssl_analysis`
- Need encoding/decoding -> `encode_decode`
- Need hash type guess -> `hash_identify`
- Need hash cracking -> `crack_hash`
- Need CVE intelligence -> `cve_lookup`
- Need to persist a security issue -> `finding_create`
- Need to review findings -> `finding_list`
- Need final engagement report -> `report_generate`
- Need brief chain-of-thought planning scaffold -> `sequentialthinking`
