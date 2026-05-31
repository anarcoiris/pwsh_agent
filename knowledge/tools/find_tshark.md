---
tools: [find_tshark, list_network_interfaces, capture_packets, analyze_pcapng]
phase: [network]
---

# find_tshark Reference Playbook

## Routing

- Use for: resolving/installing tshark when packet tools fail.
- Not for: PCAP analysis itself (use `analyze_pcapng` after tshark is available).
- Typical next tool: retry `list_network_interfaces` or `capture_packets`.

## When to Use

Wireshark's **tshark** backs `list_network_interfaces`, `capture_packets`, and `analyze_pcapng`. Consult this when those tools fail due to a missing binary.

## Resolution Order

1. `TSHARK_PATH` environment variable
2. PATH (`tshark.exe` / `tshark`)
3. `C:\Program Files\Wireshark\tshark.exe`
4. `C:\Program Files (x86)\Wireshark\tshark.exe`

## If Tools Fail with "tshark not found"

Install Wireshark (includes tshark):

```json
{"name": "host_exec", "arguments": {"command": "winget install WiresharkFoundation.Wireshark --accept-package-agreements"}}
```

Or set the path explicitly:

```json
{"name": "host_exec", "arguments": {"command": "$env:TSHARK_PATH = 'C:\\Program Files\\Wireshark\\tshark.exe'"}}
```

Then retry the failing tool (`list_network_interfaces`, `capture_packets`, or `analyze_pcapng`).

## Prefer Registered Tools

Do **not** call raw tshark via `host_exec` when registered tools work. Raw CLI is fallback only when:

- `analyze_pcapng` returns `success: false`
- You need a tshark flag not exposed by the tool API

## Fallback CLI Reference

List interfaces:
```powershell
& "C:\Program Files\Wireshark\tshark.exe" -D
```

Quick capture:
```powershell
& "C:\Program Files\Wireshark\tshark.exe" -i 2 -a duration:10 -w last_capture.pcapng
```

## Do Not Use find_tshark For

- Offline PCAP analysis when tshark is already working → use **`analyze_pcapng`**
- Replacing **`capture_packets`** or **`list_network_interfaces`** on a healthy install

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| `host_exec tshark` before trying tools | Use `analyze_pcapng` / `capture_packets` first |
| Npcap not installed | Wireshark installer prompts for Npcap — required for live capture |
| Wrong bitness path | Check both Program Files locations |
