---
phase: [general, development, recon, network, exploit]
tools: [host_exec, run_script, read_file, write_file, append_note, find_file, list_network_interfaces, capture_packets, analyze_pcapng, find_tshark, crack_hash, dns_lookup, ping_sweep, port_scan, http_headers_check, ssl_analysis, cve_lookup, system_info, encode_decode, hash_identify, finding_create, finding_list, report_generate, sequentialthinking]
---

# Tool Routing Static Reference

Use this as a compact, always-applicable "when to use" map across all tools.

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
