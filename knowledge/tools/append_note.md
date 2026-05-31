---
tools: [append_note]
phase: [development]
---

# append_note Tool Playbook

## Routing

- Use for: timestamped one-line progress in workspace note files.
- Not for: source code or full-file overwrites (use `write_file`).
- Typical next tool: continue mission tools after logging progress.

## When to Use

**Use tool `append_note`** for timestamped one-line progress on:

- `workspace/plan.md`
- `workspace/status.md`
- `workspace/session_log.md`

```json
{"name": "append_note", "arguments": {"path": "workspace/plan.md", "line": "Step 1: Script written"}}
```

## Do Not Use append_note For

- Source code deliverables → use **`write_file`**
- Overwriting entire files → append_note preserves history

WriteGuard blocks `write_file` to plan.md when a code deliverable is still pending.
