---
tools: [analyze_pcapng, find_file]
phase: [network]
---

# analyze_pcapng Tool Playbook

## Routing

- Use for: offline PCAP analysis via staged filters (broad → narrow → verbose).
- Not for: live capture (use `capture_packets`) or encoding/decoding PCAP bytes.
- Typical next tool: `read_file` for verbose log chunks.

## When to Use

**Use tool `analyze_pcapng`** after resolving the PCAP path with `find_file`. Index with a broad filter, narrow, then decode with `verbose=true`.

## Staged workflow (index → narrow → decode)

| Step | Call | Purpose |
|------|------|---------|
| 1 | `find_file(name="last_capture.pcapng")` | Resolve path (`workspace/` or `artifacts/captures/`) |
| 2 | `analyze_pcapng(file_path=…, filter_expression="http", limit=30, verbose=false)` | Index HTTP: `key_fields`, full `http_index`, `http_forms`, and `potential_plaintext_credentials` (auto-scanned) |
| 3 | `analyze_pcapng(…, filter_expression='http contains "login"', limit=15, verbose=false)` | Narrow to interest |
| 4 | `analyze_pcapng(…, filter_expression="…", limit=10, verbose=true)` | Deep packet decode (`-V`) with larger context |
| 5 | `read_file(path=verbose_log_file, line_start=1, line_count=80)` | If large dump was written to `.pulse/pcap_logs/` |

## Display filter cookbook (tshark `-Y`, not capture `-f`)

| Goal | filter_expression |
|------|-------------------|
| All HTTP | `http` |
| Login | `http contains "login" or http.request.uri contains "login"` |
| XML bodies | `http contains "xml" or http.content_type contains "xml"` |
| Passwords | `http contains "password" or ftp or smtp` |
| Single frame | `frame.number == 8` |
| Combined | `(http) and (http contains "login" or http contains "xml")` |

## Example invocations

**Index:**
```json
{"name": "analyze_pcapng", "arguments": {
  "file_path": "last_capture.pcapng",
  "filter_expression": "http",
  "limit": 30,
  "verbose": false
}}
```

**Decode login/XML:**
```json
{"name": "analyze_pcapng", "arguments": {
  "file_path": "last_capture.pcapng",
  "filter_expression": "http contains \"login\" or http contains \"xml\"",
  "limit": 10,
  "verbose": true
}}
```

## Parameters

- **file_path** — basename OK after `find_file`.
- **filter_expression** — Wireshark display filter; refine each pass.
- **limit** — keep low on first pass (20–30), smaller when `verbose=true`.
- **verbose** — `true` for full `-V` decode after narrowing filter; `key_fields` are returned even when `verbose=false`.
- **show_bytes** — raw hex (`-x`) when you need payload bytes.

## Do Not Use analyze_pcapng For

- Live packet capture → use **`capture_packets`**
- Encoding/decoding PCAP files → use filters here, not **`encode_decode`**
- Raw `tshark` via **`host_exec`** when this tool succeeds
