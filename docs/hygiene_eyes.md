# Hygiene Eyes (pwsh_agent)

pwsh_agent consumes repo-hygiene output **on demand** — not via shared code or bulk context injection.

## Feed directory

Configured in `config.yaml` → `hygiene_eyes.feed_dir` (default: `Libraries/hygiene-feed`).

repo-hygiene writes findings via `scripts/export_feed.py`. See `repo-hygiene/docs/COOPERATION.md`.

## Tool: hygiene_lookup

```json
{"name": "hygiene_lookup", "arguments": {"finding_id": "REF-001"}}
```

Returns compact excerpts from feed chunks. Use before delegating to workspace for fixes.

## Skill

`knowledge/skills/hygiene_remediation.md` is injected in prompt-pack mode when the user message matches hygiene patterns (`REF-xxx`, `hygiene`, `dead code`, etc.).

## Mission stubs

When `hygiene_eyes.poll_missions: true`, `core/sweep_loop.py` polls `hygiene-feed/missions/` and enqueues one-shot scheduled missions.

## Manual test

```powershell
cd C:\Users\soyko\Documents\Libraries\pwsh_agent
python -c "from tools.hygiene import hygiene_lookup; print(hygiene_lookup(finding_id='REF-001'))"
```
