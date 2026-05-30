---
tools: [crack_hash]
phase: [exploit]
---

# crack_hash Tool Playbook

## Description
Cracks a SHA-256 target hash using the high-performance CPU+GPU hash_pro7.py tool via a robust non-interactive runner.

## Example Invocation
```json
{
  "name": "crack_hash",
  "arguments": {
    "target_hash": "<target_hash>",
    "mask": "<mask>",
    "salt": "<salt>",
    "min_len": 10,
    "known_prefix": "<known_prefix>",
    "known_suffix": "<known_suffix>",
    "wordlist": "<wordlist>",
    "cpu_workers": 10,
    "no_gpu": false,
    "timeout": 10
  }
}
```

## Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `target_hash` | `string` | **Yes** | The target SHA-256 hash to crack (hex-encoded, 64 characters). |
| `mask` | `string` | No | Character set mask matching password format (e.g. 'NNNNNNAA!', default: 'NNNNNNAA!'). |
| `salt` | `string` | No | Optional salt concatenated to password candidate prior to hashing. |
| `min_len` | `integer` | No | Minimum incremental length to start cracking from. |
| `known_prefix` | `string` | No | Optional known string prefix to prepend to candidate passwords. |
| `known_suffix` | `string` | No | Optional known string suffix to append to candidate passwords. |
| `wordlist` | `string` | No | Optional file path to a wordlist dictionary file to search first. |
| `cpu_workers` | `integer` | No | Optional number of parallel CPU worker threads. |
| `no_gpu` | `boolean` | No | If true, completely bypasses GPU acceleration (CuPy/hashcat) and runs CPU-only. |
| `timeout` | `integer` | No | Maximum tool execution timeout in seconds. Defaults to 180. |

## Usage Notes
- Make sure to review the parameters carefully.
- Only call this tool when explicitly required by the task or when appropriate for the active exploit phase.
- Summarize the execution results back to the user in plain, concise markdown.
