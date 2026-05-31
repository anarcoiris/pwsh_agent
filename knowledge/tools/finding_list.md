---
tools: [finding_list, finding_create, report_generate]
phase: [development]
---

# finding_list Tool Playbook

## When to Use

**Use tool `finding_list`** to review persisted findings before generating a report or when the user asks what has been recorded so far.

## Example Invocations

**All findings (default limit 50):**
```json
{"name": "finding_list", "arguments": {}}
```

**Critical only:**
```json
{"name": "finding_list", "arguments": {"severity_filter": "CRITICAL", "limit": 20}}
```

**Recent high-severity batch:**
```json
{"name": "finding_list", "arguments": {"severity_filter": "HIGH", "limit": 10}}
```

## Typical Workflow

1. Run recon/analysis tools during engagement
2. `finding_create(…)` for each issue
3. `finding_list()` — verify count and severity distribution
4. `report_generate(title="Engagement Report — Example Corp")`

## Parameters

- **severity_filter** — Optional: `CRITICAL`, `HIGH`, `MEDIUM`, `LOW`, `INFO`.
- **limit** — Max rows (default 50).

## Do Not Use finding_list For

- Creating new findings → use **`finding_create`**
- Exporting formatted report → use **`report_generate`**
- Reading SQLite directly via `host_exec` → use this tool

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| Expecting empty list mid-engagement | Create findings first with `finding_create` |
| Wrong filter casing | Use uppercase severity |
