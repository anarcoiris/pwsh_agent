---
tools: [hash_identify, crack_hash]
phase: [exploit]
---

# hash_identify Tool Playbook

## When to Use

**Use tool `hash_identify`** before `crack_hash` when the user provides a hash without stating the algorithm.

## Hash Cracking Workflow

| Step | Tool | Purpose |
|------|------|---------|
| 1 | `hash_identify(hash_value=…)` | Detect MD5, SHA-256, bcrypt, etc. |
| 2 | `sequentialthinking(…)` | Plan mask, salt, prefix if rules given |
| 3 | `crack_hash(target_hash=…, …)` | Brute force (SHA-256 only) |

## Example Invocations

**Unknown 64-char hex (likely SHA-256):**
```json
{"name": "hash_identify", "arguments": {"hash_value": "18846efb090813788c3246ce05884e7155eee92186ec23569abc0c39b44b7032"}}
```

**32-char hex (likely MD5):**
```json
{"name": "hash_identify", "arguments": {"hash_value": "5d41402abc4b2a76b9719d911017c592"}}
```

**bcrypt:**
```json
{"name": "hash_identify", "arguments": {"hash_value": "$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/X4.G2oQK3K3K3K3K3"}}
```

## Recognized Patterns

MD5 (32 hex), SHA-1 (40), SHA-224 (56), SHA-256 (64), SHA-384 (96), SHA-512 (128), bcrypt (`$2a$`/`$2b$`), MD5-crypt, SHA-256-crypt, Argon2, PBKDF2-Django, LDAP `{SHA}`/`{SSHA}`, salted `hex:salt` forms.

## Do Not Use hash_identify For

- Actually cracking → use **`crack_hash`** (SHA-256 only)
- Encoding/decoding → use **`encode_decode`**
- Re-hashing plaintext → not supported

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| Skipping identify step | Always identify when algorithm is unknown |
| Expecting crack from identify | Follow with `crack_hash` for SHA-256 |
| Passing salted digest without salt to crack_hash | User's salt rules go in `crack_hash` args |
