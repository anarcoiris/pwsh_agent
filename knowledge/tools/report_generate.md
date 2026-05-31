---
tools: [report_generate, finding_create, finding_list]
phase: [development]
---

# report_generate Tool Playbook

## When to Use

**Use tool `report_generate`** at the end of an engagement after findings are recorded, or when the user asks for a formal report.

## Report Workflow

| Step | Tool | Purpose |
|------|------|---------|
| 1 | `finding_create(…)` × N | Record all issues during testing |
| 2 | `finding_list()` | Confirm findings exist |
| 3 | `report_generate(title=…)` | Write Markdown to `output/report_YYYYMMDD_HHMMSS.md` |
| 4 | `read_file(path=…)` | Review generated report if needed |

## Example Invocations

**Default report:**
```json
{"name": "report_generate", "arguments": {}}
```

**Custom title:**
```json
{"name": "report_generate", "arguments": {
  "title": "Internal Network Assessment — May 2026",
  "output_format": "markdown"
}}
```

## Output

- Path: `output/report_<timestamp>.md`
- Sorted by severity (CRITICAL → INFO)
- Executive summary with severity counts
- Per-finding sections with evidence and recommendations

## Do Not Use report_generate For

- Empty database — returns error; create findings first
- Live progress updates → use **`append_note`**
- Custom non-findings documents → use **`write_file`**

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| Generating before any `finding_create` | Record findings during engagement |
| Expecting PDF | Only markdown/text; user converts externally |
| Manual report when findings exist | Use this tool for consistent format |
