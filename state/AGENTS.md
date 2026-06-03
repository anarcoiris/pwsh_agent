# AGENTS — Specialist Roster & Handoff Rules

Six specialists. Each tool belongs to exactly one agent. LEAD orchestrates; others execute domain work.

## LEAD manager workflow

1. **Plan first** — one `sequentialthinking` max, then `append_note` on the session plan with numbered tasks.
2. **Assign agent per task** — each task line: `agent=web|workspace|forensic|recon|crypto|lead`.
3. **One delegate per turn** — call `delegate_to(agent, brief, success_criteria)` for the **current** task only. Emit **no other tool calls** in that turn.
4. **Review on return** — when `[HANDOFF COMPLETE]` appears in CURRENT STATE, update plan/status, then delegate the next task or conclude.

## Roster

**lead** — Orchestrator. Plans, delegates, records findings, generates reports. Tools: sequentialthinking, delegate_to, append_note, finding_create, finding_list, report_generate.

**workspace** — Files and scripts. Read/write/search files, run scripts. Use for code_build, code_review, scripting, file_ops, sysadmin file tasks.

**web** — HTTP and authentication. Fetch pages, test logins, inspect headers/TLS. Use for web_auth only.

**recon** — Active scanning. DNS, ping sweep, port scan, system info, CVE lookup.

**forensic** — PCAP pipeline. List interfaces, capture packets, analyze pcapng, find_tshark.

**crypto** — Hash and encoding. crack_hash, hash_identify, encode_decode.

## Routing table (advisory)

| Domain | First delegate |
|--------|----------------|
| general, mixed, reporting, conversation | lead |
| code_build, code_review, scripting, file_ops, sysadmin | workspace |
| web_auth | web |
| recon | recon |
| pcap | forensic |
| hash (crack) | crypto |

## Handoff contract

- LEAD calls `delegate_to` before specialist work (recommended). Wrong-scope tools still run with a scope advisory suggesting the correct agent.
- Specialists cannot call `delegate_to`. Control returns to LEAD automatically after one specialist action.
- Do not call `delegate_to(agent='lead')`.

## Cross-session context

- Prior sessions are not browsable by default. Use sealed handoff summaries via `session pick <id>` in console.
- LEAD reads handoff summaries, not raw prior session folders.

## Anti-patterns

- No PCAP tools on web_auth tasks.
- No report_generate without finding_create first.
- append_note is LEAD-only (specialists return facts via tool results).

## Progress notes (LEAD)

- Plan: strategy and numbered tasks with assigned agents.
- Status: completed steps and blockers.
- Scratchpad: raw logs and temporary data.
