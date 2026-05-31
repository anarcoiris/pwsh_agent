---
tools: [crack_hash]
phase: [exploit]
---

# crack_hash Tool Playbook

## Routing

- Use for: planned SHA-256 cracking with mask/salt/prefix/suffix.
- Not for: hash type identification (use `hash_identify`) or ad-hoc shell cracking.
- Typical next tool: none — plan with `sequentialthinking` first.

## When to Use

**Use tool `crack_hash`** when the user provides a SHA-256 target and password rules (mask, salt, prefix). Plan before running.

## Plan before you run

1. **target_hash** — 64 hex chars (SHA-256).
2. **salt** — appended to the password *before* hashing (`sha256(password + salt)`). If the user said “salt …”, you **must** pass `salt=`.
3. **mask** — charset per position: `N` digit, `A` letter, `L` lower, `U` upper, `!` symbol, `?` any. Default `NNNNNNAA!` = six digits + two letters + `!`.
4. **known_prefix / known_suffix** — fixed parts (e.g. prefix `xmlObj`, salt `55077791`).
5. **min_len** — when password length is known (e.g. “9 characters” → `min_len=9`).
6. One **sequentialthinking** step to state the plan, then **crack_hash** (not `host_exec`, not `-t`/`-s`).

## Example (salt + prefix)

User: crack SHA-256 `18846…` with salt xmlObj `"55077791"`

```json
{
  "name": "crack_hash",
  "arguments": {
    "target_hash": "18846efb090813788c3246ce05884e7155eee92186ec23569abc0c39b44b7032",
    "known_prefix": "xmlObj",
    "salt": "55077791",
    "mask": "NNNNNNAA!",
    "timeout": 300
  }
}
```

## Launcher

- **Preferred:** PATH `hashpro` (`py -3.10 -u hash_pro7.py`).
- **Fallback:** `artifacts/scripts/hash_pro7.py` + project venv.

## Dependencies (fallback only)

```json
{"name": "host_exec", "arguments": {"command": "& \".venv/Scripts/python.exe\" -m pip install -r artifacts/scripts/requirements-hash.txt"}}
```

## Mask charset reference (hash_pro7)

| Token | Meaning |
|-------|---------|
| `N` | digit `0-9` |
| `L` | lowercase `a-z` |
| `U` | uppercase `A-Z` |
| `A` | letters + digits |
| `!` | **punctuation charset** (many symbols) — **not** a literal `!` per position |
| `?` | any (large keyspace) |

`ULLLLLLLNN!!` → 12 chars, ~**752 trillion** candidates. Full GPU run can take **days**. Use interactive **end index** / `--end-idx` for slices, or tighten the mask.

**Digest:** `sha256(known_prefix + password + known_suffix + salt)` (mode 1410 when salt set).

## PATH hashpro vs repo copy

`hashpro.bat` on PATH may point to `…/hashcat-7.1.2/hash_pro7.py`. Sync fixes from `artifacts/scripts/hash_pro7.py` into that folder when testing manual runs.

## Common mistakes

| Mistake | Fix |
|---------|-----|
| Missing `salt` when user gave one | Always pass `salt=` |
| `host_exec hashpro -t … -s …` | Use `crack_hash` with `--target`/`--salt` mapping |
| Treating `!!` as two literal bangs | Each `!` in mask picks from punctuation charset |
| Huge mask, instant “not found” | Hashcat may exhaust a slice; use `--end-idx` / checkpoint |
| `--no-hashcat` ignored (older copies) | Update hash_pro7.py; sets `use_hashcat=False` |
| PCAP tools on hash tasks | Use only `crack_hash` |

## Do Not Use crack_hash For

- Unknown hash algorithm → use **`hash_identify`** first
- Non-SHA-256 formats → confirm type before cracking
- Direct `host_exec hashpro` with manual flags → use this tool's parameters
