# Repo Inspection Agents

Three standalone agent prompt documents for architectural governance. Each file is a complete, self-contained specification: paste it as a system prompt, feed it to Claude Code, drop it into a CI step, or use it as a standing instruction in any LLM workflow.

---

## Agents

| File | Role | When to run |
|---|---|---|
| `reviewer-agent.md` | Boundary & scope inspection | Every PR, before merge |
| `auditor-agent.md` | Version drift & artifact provenance | Weekly schedule, pre-release |
| `scaffolder-agent.md` | New module template & ADR generation | Before implementing any new module |

---

## Usage patterns

### 1. Claude Code (interactive, recommended)

```bash
# In your repo root
claude --system-prompt agents/reviewer-agent.md

# Then paste your diff:
> git diff main...my-feature | pbcopy
# Paste into Claude Code — it will run the full inspection
```

### 2. API call (CI or scripted)

```bash
SYSTEM=$(cat agents/reviewer-agent.md)
INPUT=$(git diff origin/main...HEAD)

curl -s https://api.anthropic.com/v1/messages \
  -H "x-api-key: $ANTHROPIC_API_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -H "content-type: application/json" \
  -d '{
    "model": "claude-sonnet-4-20250514",
    "max_tokens": 1500,
    "system": "'"$SYSTEM"'",
    "messages": [{"role": "user", "content": "'"$INPUT"'"}]
  }'
```

### 3. GitHub Actions

Each agent file includes a ready-to-use GitHub Actions workflow at the bottom of its `CI integration` section. Copy the YAML into `.github/workflows/`.

---

## module-map.json

A single file at the repo root that all three agents reference. The Auditor reads it to detect drift. The Reviewer reads it to understand declared boundaries. The Scaffolder writes a new entry to it.

```json
{
  "$schema": "./module-map.schema.json",
  "modules": [
    {
      "name": "auth",
      "version": "1.8.0",
      "owner": "identity-team",
      "path": "packages/auth",
      "publicSurface": ["login", "logout", "verifyToken"],
      "contractTypes": ["AuthToken", "UserSession"],
      "dependencies": {
        "internal": ["users"],
        "external": ["jsonwebtoken"]
      }
    }
  ]
}
```

Create this file at repo root and keep it updated. The Scaffolder generates the entry for each new module; you paste it in.

---

## Recommended workflow

```
New feature work
      │
      ▼
Scaffolder → generate module structure before writing code
      │
      ▼
Development
      │
      ▼
Reviewer → run on PR diff before requesting review
      │
      ▼
Merge
      │
      ▼
Auditor → runs weekly + before every release tag
```

---

## Adding repo context

All three agents accept an optional repo context block at the start of your message. Adding it once makes every inspection more precise:

```
Repo context:
Stack: TypeScript, Node.js 20, npm workspaces
Module layout: /packages/[name]/src — public surface via src/index.ts only
Boundary policy: no cross-package imports except through each package's index.ts
Versioning: semver, enforced by changesets
Changelog format: keep-a-changelog
Output directory: /dist
Lock file: package-lock.json, always committed
```

Paste this block before your diff or module description every time, or set it as a persistent instruction in your LLM client.
