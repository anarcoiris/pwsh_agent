---
tools: [encode_decode]
phase: [exploit, network]
---

# encode_decode Tool Playbook

## When to Use

**Use tool `encode_decode`** for small text blobs — URL parameters, Base64 cookies, hex dumps from logs, ROT13 puzzles. Runs in-process; no file I/O.

## Supported Encodings

| encoding | encode example | decode example |
|----------|----------------|----------------|
| `base64` | `"admin"` → `YWRtaW4=` | `YWRtaW4=` → `"admin"` |
| `base64url` | URL-safe variant | JWT payload segments |
| `hex` | `"hi"` → `6869` | `6869` → `"hi"` |
| `url` | space → `%20` | `%41%42` → `"AB"` |
| `rot13` | `"uryyb"` ↔ `"hello"` | same op both ways |
| `utf8_bytes` | `"A"` → `[65]` | `[65]` → `"A"` |

## Example Invocations

**Decode Base64 cookie value:**
```json
{"name": "encode_decode", "arguments": {"text": "YWRtaW46c2VjcmV0", "operation": "decode", "encoding": "base64"}}
```

**URL-decode query parameter:**
```json
{"name": "encode_decode", "arguments": {"text": "user%40example.com", "operation": "decode", "encoding": "url"}}
```

**Hex to ASCII:**
```json
{"name": "encode_decode", "arguments": {"text": "48656c6c6f", "operation": "decode", "encoding": "hex"}}
```

**Encode for payload construction:**
```json
{"name": "encode_decode", "arguments": {"text": "' OR 1=1--", "operation": "encode", "encoding": "url"}}
```

## Typical Workflows

**PCAP-derived string (from analyze_pcapng output, not the file itself):**
1. `analyze_pcapng(…, filter_expression="http")` — copy suspicious field value
2. `encode_decode(text=…, operation="decode", encoding="base64")`

**CTF / homework:**
1. `encode_decode` with guessed encoding
2. If result looks like a hash → `hash_identify`
3. If SHA-256 → `crack_hash`

## Do Not Use encode_decode For

- Entire PCAP files → use **`analyze_pcapng`**
- Password cracking → use **`crack_hash`**
- Hash algorithm ID → use **`hash_identify`**

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| Decoding binary files | Only pass text strings extracted from tool output |
| Wrong encoding guess | Try `base64` then `hex` then `url` |
| `host_exec [Convert]::FromBase64String` | Use this tool for consistency |
