# Plan: Inspección de capacidad de agencia

> **Status:** PROPOSED  
> **Fase:** 0 — Baseline  
> **Prioridad:** P0  
> **Roadmap maestro:** [MVF_AGENCY_ROADMAP.md](./MVF_AGENCY_ROADMAP.md)  
> **Canon:** [AGENT_CANON.md](../AGENT_CANON.md) §4, §14  
> **Depende de:** —  
> **Desbloquea:** [mvf_autonomous_plan.md](./mvf_autonomous_plan.md), [loop_restrictions_review.md](./loop_restrictions_review.md)

---

## Problema

El agente tiene pipeline multi-fase, cola unificada y plantillas nocturnas, pero no existe un **informe baseline** que mida qué tan autónomo es el bucle Razonamiento → Planificación → Acción → Probe/Validación → Introspección (R+P+A+V+I) antes de implementar R2/R4/R5/R7.

Sin baseline, no podemos demostrar progreso ni detectar regresiones tras relajar guards o cablear MVF.

---

## Objetivo

Documentar y ejecutar un harness reproducible que capture la capacidad real del agente hoy, alineado con el canon vertical y el vertical slice HelloGame.

---

## Matriz de capacidades

| Capacidad | Canon (fase) | Implementado | Ubicación | Gap |
|-----------|--------------|--------------|-----------|-----|
| **R — Razonar** | `ingest` | ✅ Parcial | `agent.py` → `_compute_intent_spec` | LLM formalizer depende de Ollama; fallback regex OK |
| **P — Planificar** | `optioneer` + PLAN | ⚠️ Parcial | `_run_vt_planning`, `IntentPlanner` | Optioneer ausente; PLAN colapsa estrategia |
| **P — Planificar** | roadmap steps | ✅ | `TaskPlanTracker.from_vt_roadmap` | — |
| **V — Validar roadmap** | pre-EXECUTE | ✅ | `core/roadmap_validator.py` | No es MVF en disco |
| **A — Actuar** | `draft` / EXECUTE | ✅ | ReAct loop, `TurnPhase.EXECUTE` | — |
| **A — Actuar** | code_build bootstrap | ✅ | `_bootstrap_code_build_mission` | Tests mocked; E2E ausente |
| **V — Validar MVF** | `validate` CPU | ❌ | — | No `mvf.json`, no `core/mvf_validator.py` |
| **I — Introspeccionar** | `refine` / EVALUATE | ⚠️ Parcial | `_mission_evaluate` | No re-encola; no cierra loop con cola |
| **Cola** | Pulse Queue | ⚠️ Parcial | `core/work_queue.py` | Sin `update_job`, notas, subtasks |
| **Scheduling** | idle/noche | ✅ | `core/orchestrator.py`, `idle_detect.py` | `checkpoint_profile` ignorado en runtime |
| **Infra GPU** | multi-GPU routing | ⚠️ Parcial | `core/model_dispatch.py`, `config.yaml` | Swap 1070 pendiente — [gpu_allocation_plan.md](./gpu_allocation_plan.md) |
| **Tool loop** | 1 acción/turno + ST | ⚠️ Parcial | `core/parser.py`, `agent.py` prompt | ST descartado si hay otra tool — [tool_loop_plan.md](./tool_loop_plan.md) |
| **Promote** | humano post-MVF | ❌ | — | Solo documentado |

**Leyenda:** ✅ funcional · ⚠️ parcial · ❌ ausente

---

## Checklist de agencia (R+P+A+V+I)

Usar esta checklist en cada corrida de inspección. Marcar PASS/FAIL/PARTIAL por ítem.

### R — Razonamiento

- [ ] `IntentSpec` persistido en `state/sessions/<id>/intent_spec.json`
- [ ] `domain` coherente con misión (p.ej. `code_build` para HelloGame)
- [ ] `deliverables[]` incluye paths esperados (`HelloGame/game.py`, `PLAN.md`)
- [ ] `success_criteria` o `done_when` no vacío
- [ ] `shadow_mode: false` tras goal lock

### P — Planificación

- [ ] `_task_plan` con ≥2 steps tras `_run_vt_planning`
- [ ] Roadmap VALIDATE status ≠ `reject` (o replan documentado)
- [ ] `CURRENT STATE` muestra `[MANAGER PLAN]` o equivalente
- [ ] Steps tienen `tool_hint` útil (`write_file`, `run_script`)

### A — Acción

- [ ] Al menos un `write_file` a path de deliverable
- [ ] Specialist `workspace` activo para code_build
- [ ] `delegate_to` usado si LEAD inicia code_build
- [ ] Herramientas sustantivas registradas en `_mission_tools_executed`

### V — Probe / Validación

- [ ] Artefactos existen en disco (paths del IntentSpec)
- [ ] `run_script` o `host_exec` ejecutó pytest (hoy: ad hoc)
- [ ] MVF checks CPU pasaron (post-R4: `mvf.json` → `validated: true`)

### I — Introspección

- [ ] `_mission_evaluate` invocado tras batch exitoso (si config lo permite)
- [ ] `append_note` en plan/status/scratchpad (LEAD)
- [ ] `_final_synthesis()` produce resumen coherente
- [ ] Job en cola → `status=done` y sin `last_error` (post-orquestador)
- [ ] Re-encolado o nota en cola (post [pulse_queue_agency_plan.md](./pulse_queue_agency_plan.md))

---

## Harness de inspección

### Pre-requisitos

- Python 3.10+ con venv activo (`.venv\Scripts\activate`)
- Ollama multi-GPU activo (ver [memory/2026-06-19.md](../../memory/2026-06-19.md))
- **R1c (recom.):** swap 1070 aplicado — [gpu_allocation_plan.md](./gpu_allocation_plan.md) Decisión A
- Verificación 3 puertos: [MVF_AGENCY_ROADMAP.md](./MVF_AGENCY_ROADMAP.md) §4.4
- **T1 audit (pre-cambio parser):** contar raw vs picked tool_calls en `llm_audit.jsonl` — [tool_loop_plan.md](./tool_loop_plan.md) T-G1
- **Un solo proceso agente** (no `pulse` + daemon simultáneos — WinError 32 en session.db)
- Sin jobs hygiene concurrentes (invalidan probe de latencia)
- Repo root: `c:\Users\soyko\Documents\Libraries\pwsh_agent`

### Paso 1 — Smoke vertical (sin EXECUTE)

```powershell
cd c:\Users\soyko\Documents\Libraries\pwsh_agent
py -3.10 tools_dev\vertical_smoke.py
```

**Capturar:** `source`, `domain`, `objectives`, `steps`, `VALIDATE status`, `issues`.

**Éxito mínimo:** imprime `CHAIN OK`.

### Paso 2 — Estado de cola y condiciones

```powershell
py -3.10 pulse_queue.py status
py -3.10 pulse_queue.py list
```

**Capturar:** idle seconds, night window, conteos por status.

### Paso 3 — Encolar HelloGame (sin ejecutar aún)

**Usar plantilla YAML** — no el wizard `add` con texto libre (anti-patrón: `"hello_game template"` → JS en `hello_game/`).

Desde consola Pulse (`console.py` → `queue` → `template` → `hello_game`) o:

```python
from core.queue_templates import enqueue_from_template
jid = enqueue_from_template("hello_game")
print(jid)
```

Activar venv antes: `.venv\Scripts\activate` (requiere `croniter`).

**Capturar:** job id, `priority`, `requires_idle_seconds`, `checkpoint_profile`.

### Paso 4 — Dispatch forzado (E2E parcial)

```powershell
py -3.10 pulse_queue.py run-once
```

**Capturar:**

| Métrica | Dónde leer |
|---------|------------|
| Job ejecutado | stdout daemon / `list --all` |
| `last_error` | `.pulse/work_queue.db` vía `list_jobs(include_done=True)` |
| Session id | `state/active_session.json` |
| Fases LLM | `state/sessions/<id>/llm_audit.jsonl` |
| Tools ejecutados | audit trail / session log |
| `MISSION_COMPLETE` | grep en audit o logs |
| Artefactos disco | `HelloGame/` tree |
| Checkpoints bloqueados | ¿daemon esperó stdin? |

### Paso 5 — Revisión de sesión

Archivos a inspeccionar:

```
state/sessions/<id>/intent_spec.json
state/sessions/<id>/CURRENT_STATE.md
state/sessions/<id>/llm_audit.jsonl
state/sessions/<id>/mvf.json          # post-R4
state/sessions/<id>/operator_inbox.jsonl  # post-R2
workspace/sessions/<id>/plan_*.md
workspace/sessions/<id>/status_*.md
```

### Paso 6 — Tests unitarios relacionados (sin LLM)

```powershell
py -3.10 -m pytest tests/test_hello_game_mission.py tests/test_work_queue.py tests/test_roadmap_validate.py -q
```

---

## Plantilla de informe baseline

Copiar y completar tras cada corrida:

```markdown
## Agency baseline — YYYY-MM-DD

### Entorno
- Ollama: [up/down]
- Daemon: [pulse_queue / console sweep / manual run-once]
- Template: [hello_game / custom]

### Resultados R+P+A+V+I
| Fase | PASS/PARTIAL/FAIL | Notas |
|------|-------------------|-------|
| R    |                   |       |
| P    |                   |       |
| A    |                   |       |
| V    |                   |       |
| I    |                   |       |

### Métricas
- tools_executed: N
- turns EXECUTE solo-ST (sin tool sustantiva): N — baseline T-G2
- raw tool_calls vs picked (parser): N descartados — baseline T-G1
- MISSION_COMPLETE: accepted/rejected/n/a
- mvf.validated: true/false/n/a
- job status: pending/running/done/failed
- HelloGame artifacts: [list or none]

### Blockers observados
1. ...

### Acción siguiente
- [ ] mvf_autonomous_plan
- [ ] mvf_validator_plan
- [ ] loop_restrictions_review
```

---

## Criterios de éxito (Fase 0)

1. Este plan existe y la matriz de capacidades está confirmada contra código actual.
2. Al menos **una corrida** del harness Pasos 1–2 documentada (informe baseline en `memory/YYYY-MM-DD.md` o sección abajo de este doc).
3. Gaps P0 identificados y enlazados a planes hijos:
   - [mvf_autonomous_plan.md](./mvf_autonomous_plan.md)
   - [mvf_validator_plan.md](./mvf_validator_plan.md)
   - [loop_restrictions_review.md](./loop_restrictions_review.md)
   - [gpu_allocation_plan.md](./gpu_allocation_plan.md) (R1c, recom. antes R5)
   - [tool_loop_plan.md](./tool_loop_plan.md) (T1, audit paralelo R4)

---

## Observaciones (inspección 2026-06-19)

1. **`HelloGame/` no existe en disco** — misión nunca completó E2E.
2. **`checkpoint_profile: mvf_autonomous`** en plantilla no aplicado por orquestador.
3. **Dos colas:** hygiene feed → `scheduler.db`; Pulse Queue → `work_queue.db`.
4. **Tests HelloGame** cubren bootstrap/planning mockeados, no `run_mission` live.
5. **`vertical_smoke.py`** valida INTAKE→PLAN→VALIDATE roadmap únicamente.

---

## Referencias

| Documento | Rol |
|-----------|-----|
| [AGENT_CANON.md](../AGENT_CANON.md) | Pipeline 10 fases, MVF, goal lock |
| [MVF_AGENCY_ROADMAP.md](./MVF_AGENCY_ROADMAP.md) | Roadmap maestro implementación |
| [ORCHESTRATOR_CANON.md](../ORCHESTRATOR_CANON.md) | Pulse Queue |
| [hello_game_e2e_plan.md](./hello_game_e2e_plan.md) | E2E objetivo |
| [gpu_allocation_plan.md](./gpu_allocation_plan.md) | Swap GPU, tool_agent |
| [tool_loop_plan.md](./tool_loop_plan.md) | Parser, ST, concurrencia |
| [loop_restrictions_review.md](./loop_restrictions_review.md) | Guards conflictivos |

---

## Baseline runs

### 2026-06-19 — daemon + wizard manual (parcial)

| Fase | Resultado | Notas |
|------|-----------|-------|
| R | PARTIAL | IntentSpec no verificado; misión mal formulada |
| P | FAIL | Sin roadmap HelloGame Python |
| A | PARTIAL | Creó `hello_game/greet.js` (incorrecto) |
| V | FAIL | Sin pytest; sin MVF |
| I | FAIL | SQLite corrupto; job status desconocido |

**Entorno:** `pulse_queue.py daemon` tras wizard `add` con texto `"hello_game template"`.  
**Blockers:** WinError 32 session.db; paths `hello_game/` vs canon `HelloGame/`; R2/R4 pendientes.  
**Detalle:** [MVF_AGENCY_ROADMAP.md](./MVF_AGENCY_ROADMAP.md) §8.

### 2026-06-19 — consola `ejemplos-texto/` (falso éxito — referencia R4b)

| Fase | Resultado | Notas |
|------|-----------|-------|
| R | FAIL | Thought 1 arrancó con REF-001 (contaminación hygiene) |
| P | FAIL | Sin plan para 100 `.md` |
| A | FAIL | `host_exec` ×3 PowerShell roto (`$i` perdido); `append_note` mentiroso |
| V | FAIL | Sin directorio `ejemplos-texto/`; MVF `dir_count` no existía aún |
| I | FAIL | `Mission complete` por `max_steps` sin gate MVF |

**Síntomas:** specialist/workspace bloqueos LEAD-only; misión declarada completa sin entregables.  
**Fix aplicado:** R4b exit gate, `delivery_probe`, `dir_count` MVF, sweep agent aislado, hygiene fence.
