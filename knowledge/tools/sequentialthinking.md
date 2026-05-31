---
tools: [sequentialthinking]
phase: [general]
---

# sequentialthinking Tool Playbook

## When to Use

**Use tool `sequentialthinking`** for explicit planning steps before complex multi-tool workflows — hash cracking plans, PCAP analysis strategy, recon sequencing. It is local Python state; not a substitute for executing real tools.

## Good Use Cases

- State crack_hash mask/salt plan before running
- Outline PCAP filter refinement passes (broad → narrow → verbose)
- Revise approach when a tool returns unexpected results (`isRevision: true`)

## Example Invocations

**Plan hash crack (then call crack_hash):**
```json
{"name": "sequentialthinking", "arguments": {
  "thought": "Hash is SHA-256 (64 hex). User said salt xmlObj 55077791 and mask 6 digits + 2 letters + !. Will call crack_hash with known_prefix=xmlObj, salt=55077791, mask=NNNNNNAA!.",
  "thoughtNumber": 1,
  "totalThoughts": 2,
  "nextThoughtNeeded": true
}}
```

**Final thought — proceed to action:**
```json
{"name": "sequentialthinking", "arguments": {
  "thought": "Plan confirmed. Executing crack_hash now.",
  "thoughtNumber": 2,
  "totalThoughts": 2,
  "nextThoughtNeeded": false
}}
```

**Revise after tool failure:**
```json
{"name": "sequentialthinking", "arguments": {
  "thought": "port_scan range failed — nmap not installed. Will retry with comma-separated common ports instead.",
  "thoughtNumber": 3,
  "totalThoughts": 4,
  "nextThoughtNeeded": true,
  "isRevision": true,
  "revisesThought": 2
}}
```

## Parameters

- **thought** — Current reasoning step (required).
- **thoughtNumber** / **totalThoughts** — 1-based progress (required).
- **nextThoughtNeeded** — `false` on last thought before tool execution (required).
- **isRevision** / **revisesThought** — When correcting earlier plan.

## Do Not Use sequentialthinking For

- Pretending a tool ran successfully — always call the real tool
- Replacing `append_note` progress logs
- Long chains without follow-up tool calls — plan then act

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| Only thinking, never executing | End with `nextThoughtNeeded: false` then call the tool |
| Meta-thought after parser salvage | Real tool calls take priority over new thoughts |
| 10+ thoughts on simple tasks | One plan thought + one action is enough |
