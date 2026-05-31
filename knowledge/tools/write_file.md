---
tools: [write_file]
phase: [development]
---

# write_file Tool Playbook

## Routing

- Use for: code/document deliverables that must exist on disk.
- Not for: progress logs (use `append_note`).
- Typical next tool: `run_script` or `read_file`.

## When to Use

**Use tool `write_file`** for user-requested source files (`.py`, `.ps1`, reports).

Always write to the exact path the user named (e.g. `watcher/watcher.py`), not `workspace/plan.md`.

## PowerShell Sanitizer

`.ps1` content is auto-sanitized on write (trailing backtick line-continuation fixes). No manual fix needed.

## Do Not Use write_file For

- Progress/status lines → use **`append_note`** on `workspace/plan.md` or `workspace/status.md`
- Short mission notes under 500 chars without code markers
