# Multi-Purpose Agent Design — From One-Trick Pony to General Operator

**Status:** Design / proposal (no behavior changes yet, except the already-shipped `crack_hash` crash fix)
**Author:** drafted with the agent, grounded in code review of `agent.py`, `core/task_intent.py`, `core/chat_goals.py`, `core/task_plan.py`, `core/context_router.py`, `core/llm_utils.py`.
**Builds on:** `consolidated_generalization_plan.md` (defensive de-biasing — partly shipped). This document is the *constructive* successor: instead of "stop forcing the hash trick," it defines the substrate that lets the agent do **any** task naturally.

---

## 1. The triggering incident

User asked, in HOST mode:

> "plan a way to try user: `user` and password: `<contents of workspace/pwd.txt>`. The site is `http://192.168.1.1`"

The agent read the file correctly, then:

1. `hash_identify("321123Aa!")` — treated a **plaintext password** as a hash.
2. `crack_hash("321123Aa!", mask="N=6 A=2 !=1")` — tried to brute-force the plaintext it already had. The call also crashed (`name 'result' is not defined`).
3. Looped `sequentialthinking` with vacuous one-liners and never attempted the actual login.

The task was *"try these credentials against a web login."* The agent never even tried, because it has no concept of that task and no tool for it — so it pattern-matched to the only fully-tooled workflow it owns: **PCAP → extract → crack_hash**.

---

## 2. Root cause: the "one trick" is wired into five layers

The hash/PCAP credential pipeline is not just a set of tools — it is hard-coded into every decision layer. Everything else falls through to thin or hash-flavored defaults.

| Layer | File | What it does | One-trick bias |
|---|---|---|---|
| Intent classify | `core/task_intent.py` `detect_mission_kind()` | regex → `{hash, pcap, dev, file_find, recon, general}` | No `web/auth/login/sysadmin/codereview` classes. "general" is a dead-end. |
| Goal gating | `core/chat_goals.py` | regex registry → `required_tools`/`blocked_tools` | Only **pcap / portscan / hashcrack / credential** builders exist. All else → `None`. |
| Planning | `core/task_plan.py` `_parse_steps_from_prompt()` | regex → fixed `TaskStep`s | Steps are literally `read_context, extract_secrets, write_deliverable, crack_hash`. Only generic fallback is "write a .py/.ps1". |
| Tool routing | `core/context_router.py` `_derive_tool_set()` | keyword → tool-group playbooks | **`password` → `_EXPLOIT_TOOLS` (crack_hash, hash_identify)**. A bare URL/`login` routes to *nothing*. ← the smoking gun for this incident. |
| Phase directive | `core/llm_utils.py` `build_context()` | per-phase nudge | Default fallback (L499) is **`RECONNAISSANCE → run port_scan/dns_lookup`**, forcing network tools on unrelated tasks. |
| Missing capability | `tools/recon.py`, `tools/intel.py`, `tools_legacy.py` | — | **No login/credential-test tool, no code-review tool, no scheduled-task tool.** `_WEB_TOOLS` = headers + ssl only. |

**Consequence:** when a task isn't PCAP/hash, the model is handed hash playbooks, a recon nudge, no plan, and no relevant tool. The only coherent path it can see is the hash trick.

The `consolidated_generalization_plan.md` correctly softened the *forcing* (PARTIAL state, dev-intent exclusions — already in code). But softening removes the wrong behavior; it does not add the right one. The agent still has no machinery to *understand* and *execute* a general task.

---

## 3. Design principles

1. **Intent first, regex last.** A single structured interpretation of the user's request drives every downstream layer. Keyword regexes become *fallback hints*, never the primary router.
2. **Natural tool feedback, not forced tools.** Replace hard `blocked_tools`/`required_tools` rails with *advisory* context + honest tool results. The model decides; guards exist only for safety and obvious mistakes.
3. **Capabilities, not hardcoded tools.** Plans reference *what needs to happen* (capability), and a registry resolves which tool serves it. Adding a domain = registering tools + capability tags, not editing five regex blocks.
4. **Structured reasoning + smart retrieval.** A real ReAct loop: interpret → plan → act → observe → reflect → adapt, with retrieval surfacing the *relevant* tools/playbooks/memory for the active intent.
5. **Safety scales with scope, not with task type.** HOST mode, network egress, and destructive ops gate on a per-action policy — independent of whether the task is "pentest" or "sysadmin."

---

## 4. Target architecture

```
user message
   │
   ▼
┌──────────────────────────────────────────────┐
│ 4.1 Intent Formalizer  (NEW — the centerpiece)│  message → IntentSpec
│   LLM call → structured "declaration of intent"│
│   regex heuristics as seed/fallback            │
└───────────────┬────────────────────────────────┘
                │ IntentSpec
        ┌───────┼───────────────┬──────────────────┐
        ▼       ▼               ▼                  ▼
   4.2 Capability   4.3 Generic     4.4 Soft-guidance   4.5 Completion
   Registry +       Planner         ReAct loop          Evaluator
   Smart Retrieval  (capability     (advisory ctx,      (criteria from
   (tools/playbooks  steps, not      reflection,        IntentSpec, not
    for this intent) crack_hash)     adaptation)        crack_hash gates)
```

### 4.1 Intent Formalizer — *"translate the human order into a formal declaration of intentions"*

This is exactly the starting point the user named. New module `core/intent_spec.py`.

**Schema (`IntentSpec`):**

```python
@dataclass
class IntentSpec:
    raw: str                       # original user message
    summary: str                   # one-line restatement of the goal
    domain: str                    # web_auth | recon | pcap | hash | code_review |
                                   #   code_build | scripting | sysadmin | file_ops |
                                   #   reporting | conversation | mixed
    objectives: list[str]          # ordered, concrete sub-goals
    targets: list[str]             # hosts, URLs, files, dirs the task acts on
    inputs: dict[str, str]         # resolved params (e.g. {"user": "...", "password_source": "workspace/pwd.txt"})
    deliverables: list[str]        # files/artifacts the user expects
    constraints: list[str]         # "don't touch network", "only in dir X", scope limits
    success_criteria: list[str]    # how we know we're done — drives 4.5
    capabilities: list[str]        # capability tags the planner will need (see 4.2)
    safety: SafetyAssessment       # egress? destructive? needs user confirm?
    confidence: float
    needs_clarification: list[str] # questions to ask if ambiguous
```

**How it's produced:**
- A dedicated LLM call (cheap/fast model is fine) with a strict JSON schema and few-shot examples covering *all* domains — not just hash/pcap. Output validated; on parse failure, fall back to a regex-seeded `IntentSpec` built from the existing `detect_mission_kind()` + `TaskIntentExtractor`.
- Regex extractors (`task_intent.py`) are reused to *seed* fields (deliverables, filename globs, targets) so the LLM has structured hints and we keep a deterministic fallback.
- The result is **persisted** to `state/sessions/<id>/intent_spec.json` and injected into context as a `### DECLARED INTENT ###` block — a single, authoritative statement of what we're doing, replacing the scattered phase/goal/roadmap guesses.

**Worked example (the incident):**

```json
{
  "summary": "Attempt an HTTP login to 192.168.1.1 with a given user and a password read from a file.",
  "domain": "web_auth",
  "objectives": ["read password from workspace/pwd.txt", "attempt login to http://192.168.1.1 as 'user'", "report whether auth succeeded"],
  "targets": ["http://192.168.1.1", "workspace/pwd.txt"],
  "inputs": {"user": "user", "password_source": "workspace/pwd.txt"},
  "capabilities": ["file_read", "http_auth_attempt"],
  "success_criteria": ["a definitive auth result (success/fail) with the HTTP status/evidence"],
  "safety": {"network_egress": true, "needs_confirm_in_host_mode": true},
  "confidence": 0.9
}
```

With this, no layer ever sees "password" and reaches for `crack_hash`. The capability `http_auth_attempt` deterministically routes to a web-auth tool (4.2 / §5).

### 4.2 Capability Registry & Smart Retrieval

New module `core/capabilities.py`. Each tool declares metadata:

```python
@capability(
    name="http_auth_attempt",
    domains=["web_auth"],
    summary="Try credentials against an HTTP endpoint (Basic + common form logins).",
    when_to_use="User wants to test/verify a username+password against a web service.",
    safety=Safety(network_egress=True),
)
def try_http_login(...): ...
```

- `context_router._derive_tool_set()` is rewritten to resolve **`IntentSpec.capabilities` → tools** via the registry, with keyword regex kept only as a low-priority fallback. The line `password → _EXPLOIT_TOOLS` is deleted.
- **Smart retrieval:** RAG playbook/domain lookup (`core/rag.py`) is queried with the `IntentSpec.summary` + `domain` + `capabilities`, not the raw message — so a web-auth task retrieves web-auth playbooks, a code-review task retrieves review checklists, etc.
- Tool *schemas presented to the LLM* can be filtered/ordered by relevance to the active intent (reduces the bias of always showing the full hash arsenal first).

### 4.3 Generic Planner

Replace `TaskPlanTracker._parse_steps_from_prompt()` (regex → crack_hash steps) with a planner that builds steps from `IntentSpec.objectives`, each tagged with a **capability** rather than a literal tool:

```python
TaskStep(id="attempt_login", label="Try credentials against the endpoint",
         capability="http_auth_attempt")     # tool resolved at run time via registry
```

- The hardcoded `read_context / extract_secrets / write_deliverable / crack_hash` template becomes **one registered domain plan among many** (the `pcap`/`hash` plan), not the default.
- Plans for new domains (`code_review`, `scripting`, `sysadmin`, `file_ops`) are registered the same way.
- `min_steps`, completion, and readaptation logic stay, but key off capabilities + `success_criteria` instead of crack_hash.

### 4.4 Unified soft-guidance ReAct loop

In `agent.py:chat_turn()` (the loop around L1626–1708+):

- **Inject** the `### DECLARED INTENT ###` block + the capability-resolved plan + retrieved playbooks (via `ContextRouter.build_injections`).
- **Demote hard gates to advisory feedback.** `ChatGoalGuard`'s `blocked_tools`/`required_tools` become *nudges in the tool result* ("this tool rarely fits a web_auth task; consider `try_http_login`") rather than hard blocks. Keep **only** safety blocks (egress/destructive) and the anti-loop dedup as hard rails.
- **Reflection step:** after each tool result, a lightweight observe/reflect note updates plan-step status and decides next action — this is where "natural feedback of tool usage" lives. Honest tool errors (like the `crack_hash` crash) feed back as adaptation signals instead of silent loops.
- Remove the `RECONNAISSANCE` default nudge for non-recon intents (the `build_context` fallback already has a `GENERAL / ANALYSIS` branch at L470 — make it the default for `domain == conversation/code_*/file_ops/sysadmin`).

### 4.5 Generic completion evaluation

`core/mission_evaluator.py` / completion guards evaluate against `IntentSpec.success_criteria` and deliverable existence — domain-agnostic. "Done" for a code review = a review artifact produced; for web_auth = a definitive auth result; for scripting = the `.ps1` exists and (optionally) ran. No more crack_hash-shaped completion logic on the general path.

---

## 5. New capability domains to unlock (per the user's goals)

Each is a registry entry + (optionally) one new tool + a domain plan. None require touching the five regex layers once 4.1–4.5 exist.

| Domain | Capability tags | New tool? | Notes |
|---|---|---|---|
| **Web auth / login test** | `http_auth_attempt` | `try_http_login(url, user, password)` — PS `Invoke-WebRequest` Basic + common form POST | **Safety-gated** (network egress, HOST confirm). Directly fixes the incident. |
| **Code review** | `code_review`, `static_scan` | optional `review_file(path, focus)` wrapper; mostly `read_file`+`grep_file`+reasoning | Produces a review artifact (findings list / inline notes). |
| **Code build / propose in another dir** | `code_build`, `scaffold` | reuse `write_file`/`run_script`; add dir-scoped write capability | Honor `IntentSpec.constraints` (target dir); `WriteGuard` extended to allow declared external dirs. |
| **PowerShell script building** | `scripting` | reuse `write_file` + `run_script`/`host_exec` | Lint/dry-run via `host_exec pwsh -NoProfile -Command "..."` before saving. |
| **Scheduled task manager** | `sysadmin`, `task_schedule` | `manage_scheduled_task(action, name, ...)` wrapping `schtasks`/`Register-ScheduledTask` | Safety-gated (system modification). |
| **File revision** | `file_ops`, `file_edit` | structured edit tool (read→patch→write) | Diff-style edits with `WriteGuard` confirmation. |

---

## 6. Phasing (low-risk, incremental)

| Phase | Deliverable | Risk | Status |
|---|---|---|---|
| **0** | Fix `crack_hash` `name 'result' is not defined` (`tools_legacy.py`) | — | ✅ done |
| **1** | `core/intent_spec.py` + `IntentSpec`, LLM formalizer w/ regex fallback, `intent_spec.json` persistence. Shadow mode: compute + log. | Low | ✅ done |
| **2** | `core/capabilities.py` registry; `context_router._derive_tool_set()` migrated to capability resolution (`password→_EXPLOIT_TOOLS` removed); login/web routing added. | Medium | ✅ done |
| **3** | Domain-aware planner: `web_auth` plan (read creds → `try_http_login`); `try_http_login` is a terminal step (no readaptation loop). Forensic prompts (hash/pcap) unchanged. | Medium | ✅ done |
| **4** | `web_auth` phase directive added; `RECONNAISSANCE` catch-all softened (tool use optional for conversational/planning). Full `ChatGoalGuard` demotion to advisory: **pending**. | Medium-High | ◑ partial |
| **5** | `try_http_login` tool (Basic + form) wired end-to-end (registry, schema, MCP, capability). scripting/file_ops/code_review use existing tools; dedicated scheduled-task tool: **pending**. | Per-tool | ◑ partial |
| **6** | `### DECLARED INTENT ###` context injection (domain, objectives, success criteria, safety) so the agent self-directs and judges completion against criteria. Dedicated criteria-based completion evaluator replacing crack_hash gates: **pending**. | Medium | ◑ partial |

**Remaining follow-ups:** (a) full `ChatGoalGuard` soft-guidance + per-result reflection step; (b) dedicated `manage_scheduled_task` tool and `code_review` artifact tool; (c) a generic completion evaluator keyed on `success_criteria`; (d) validate the LLM formalizer once the `chat-analyzer` Ollama model is reachable (the deterministic fallback is confirmed working; LLM refinement now runs non-blocking in the background).

---

## 7. Safety & scope

- HOST mode + `SafetyAssessment.network_egress` or `destructive` ⇒ require explicit user confirmation before the action (aligns with the workspace `AGENTS.md` "ask before anything that leaves the machine").
- `try_http_login` and `manage_scheduled_task` ship behind this gate by default.
- `WriteGuard`/`ExecutionPolicy` extended to read `IntentSpec.constraints` (e.g. "only write under `proj/`") and `targets` to scope file/dir operations.

---

## 8. Testing strategy

- **Intent formalizer:** golden-set of ~20 prompts across all domains → assert `domain` + `capabilities` + `deliverables`. Must classify the incident prompt as `web_auth` / `http_auth_attempt`, **never** `hash`.
- **Capability routing:** assert `password` alone no longer surfaces `crack_hash`; `http://…/login` surfaces web-auth tools.
- **Regression:** all existing tests (`test_chat_goals`, `test_task_plan`, `test_context_trim`, `test_completion_guards`, hash/pcap mission tests) must stay green — the pcap/hash workflow becomes one registered domain and must behave identically.
- **Soft-guidance:** a `web_auth` prompt completes by attempting login (or asking for confirmation in HOST mode), not by cracking.
- **No-regression on the trick:** `"crack this sha256 hash …"` still routes to the full crack pipeline.

---

## 9. Summary

The agent is a one-trick pony because *understanding, planning, routing, and completion* are all hardcoded to the hash/PCAP pipeline, and unknown tasks fall through to hash-flavored defaults. The fix is not more special cases — it is a single **intent formalization** front-end that turns the user's words into a structured declaration, feeding a **capability-based** planner, a **soft-guidance** ReAct loop, and a **criteria-based** completion check. The existing pentest pipeline survives unchanged as *one domain among many*, and new domains (web-auth, scripting, code review, sysadmin, file ops) become registrations rather than rewrites.
