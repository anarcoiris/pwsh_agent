---
tools: [append_note]
phase: [development]
---

# append_note Tool Playbook

## Progress Logs Only

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
