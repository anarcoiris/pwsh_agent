---
tools: [append_note]
phase: [development]
---

# append_note Tool Playbook

## Routing

- Use for: timestamped one-line progress in workspace note files.
- Not for: source code or full-file overwrites (use `write_file`).
- Typical next tool: continue mission tools after logging progress.

## Note Domains

To avoid redundancy and prompt bloat, each note type has a strict domain. Do NOT cross-post or duplicate contents:

- **Plan** (`workspace/plan.md` or `plan_{id}.md`): High-level strategy, milestones, and next steps. Do NOT log raw tool output, credentials, or execution logs.
- **Status** (`workspace/status.md` or `status_{id}.md`): Step/milestone completions, error resolutions, and blockers. Do NOT use `write_file` to status notes.
- **Scratchpad** (`workspace/sessions/{id}/scratchpads/*.md`): Raw CLI logs, command outputs, temporary data, and credentials. Do NOT log high-level status updates.

Appending the same or similar information to multiple note types is redundant and penalized.

```json
{"name": "append_note", "arguments": {"path": "workspace/plan.md", "line": "Strategy adjusted: switching to manual port scanning after tool timeout"}}
```

## Do Not Use append_note For

- Source code deliverables → use **`write_file`**
- Overwriting entire files → append_note preserves history


WriteGuard blocks `write_file` to plan.md when a code deliverable is still pending.
