---
tools: [hygiene_lookup, delegate_to, read_file, write_file, grep_file, run_script, host_exec]
phase: [hygiene, development]
---

# Hygiene Remediation Skill

When the user mentions hygiene findings, dead code, REF-xxx, ARCH-xxx, DEP-xxx, or repo maintenance:

1. Call `hygiene_lookup` with `finding_id` or a short `query` — do not read full ai_review reports.
2. `delegate_to` workspace with a brief containing the file path and suggested action from the lookup excerpt.
3. Workspace loop: `read_file` → `write_file` patch → `run_script` or `host_exec` to verify.
4. Confirm the finding is resolved before completing the turn.

Never paste entire hygiene reports into `append_note`. Use lookup excerpts only.

## Example

```json
{"name": "hygiene_lookup", "arguments": {"finding_id": "REF-001"}}
```

Then delegate:

```json
{"name": "delegate_to", "arguments": {"agent": "workspace", "brief": "Remove unused import in core/parser.py per REF-001, then run pytest."}}
```
