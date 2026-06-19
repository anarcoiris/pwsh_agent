# Plan: Revisión de restricciones conflictivas del bucle

> **Status:** PROPOSED  
> **Fase:** 0b — Revisión pre-implementación  
> **Prioridad:** P0  
> **Roadmap maestro:** [MVF_AGENCY_ROADMAP.md](./MVF_AGENCY_ROADMAP.md)  
> **Canon:** [AGENT_CANON.md](../AGENT_CANON.md) §2 (goal lock, MVF-first)  
> **Depende de:** [agency_audit_plan.md](./agency_audit_plan.md)  
> **Desbloquea:** [mvf_validator_plan.md](./mvf_validator_plan.md), [probe_test_refine_plan.md](./probe_test_refine_plan.md), [hello_game_e2e_plan.md](./hello_game_e2e_plan.md)

---

## Problema

El bucle de misión acumula **guards deterministas** (conteo mínimo de tools, un tool por turno, checkpoints bloqueantes, validación de roadmap) que fueron útiles para misiones de auditoría pero **compiten** con la salida natural de misiones `code_build` y con el canon MVF-first:

- El agente puede ejecutar correctamente HelloGame pero **no declarar** `MISSION_COMPLETE` por umbrales arbitrarios.
- Puede declarar `MISSION_COMPLETE` **sin** pytest verde porque no hay gate MVF.
- El daemon nocturno puede **colgarse** en `input()` por checkpoints parcialmente relajados en `headless`.

Este plan inventaria restricciones, evalúa conflicto, y propone relajación **coordinada** con R4 (MVF validator) para no dejar vacío de seguridad.

---

## Principio rector

**Reemplazar heurísticas opacas por verdad determinista (MVF checks), manteniendo guards de seguridad (WriteGuard, SANDBOX, ops destructivas).**

Orden de precedencia propuesto:

1. MVF `validated === true` → permitir `MISSION_COMPLETE`
2. Si no hay MVF definido → fallback heurístico actual (con umbrales relajados por `mission_kind`)
3. Seguridad puntual → siempre bloquea (destructive, policy)

---

## Inventario de restricciones

### 1. Conteo mínimo de tools antes de completar

| Campo | Valor | Ubicación |
|-------|-------|-----------|
| `MIN_TOOLS_BEFORE_COMPLETE` | `4` | `agent.py` ~L356 |
| `MIN_SUBSTANTIVE_BEFORE_COMPLETE` | `2` | `agent.py` ~L357 |
| Aplicación | Gate en `MISSION_COMPLETE` | `agent.py` ~L2424–2468 |

**Conflicto:** HelloGame MVF realista = `write_file` (PLAN) + `write_file` (game) + `write_file` (test) + `run_script` (pytest) = 4 tools, pero algunos turns son solo `append_note` o `read_file`, y el contador puede no alinear con "misión cumplida".

**Severidad:** Alta para code_build simple.

### 2. MISSION_COMPLETE sin gate MVF

| Comportamiento | Ubicación |
|----------------|-----------|
| Acepta complete si tools ≥ min AND objective_ok AND substantive_ok | `agent.py` ~L2431–2459 |
| No verifica pytest ni `file_exists` | — |

**Conflicto:** Permite cierre prematuro; contradice P5 del canon ("Tests como verdad").

**Severidad:** Crítica.

### 3. "Emit ONE ACTION tool call per turn"

| Ubicación | `agent.py` system prompt ~L641 |
|-----------|--------------------------------|
| Excepción | Múltiples `append_note` en mismo turn OK |

**Conflicto:** Ralentiza code_build; el parser ya deduplica acciones idénticas. `sequentialthinking` queda descartado si el modelo emite otra tool en el mismo turno — ver [tool_loop_plan.md](./tool_loop_plan.md) (T1-A/B/D).

**Severidad:** Media (throughput, no corrección). Implementación detallada en T1, no solo prompt.

### 4. Un delegate por turn (LEAD)

| Ubicación | `state/AGENTS.md` — LEAD workflow |
|-----------|-----------------------------------|
| Regla | "One delegate per turn — no other tool calls" |

**Conflicto:** Impide LEAD de anotar plan y delegar en mismo turn; fuerza turnos extra.

**Severidad:** Baja–media (latencia).

### 5. Specialist soft scope / chat_goals blocks

| Ubicación | `agent.py`, `core/chat_goals.py` |
|-----------|----------------------------------|
| Efecto | Advisory o hard block de tools fuera de scope |

**Conflicto:** LEAD bloqueado en tools de workspace en code_build si routing incorrecto.

**Severidad:** Media (depende de misión).

### 6. WriteGuard / append_note domain

| Ubicación | `core/write_guard.py`, `knowledge/tools/append_note.md` |
|-----------|--------------------------------------------------------|
| Efecto | Bloquea `write_file` a paths de notas; exige `append_note` |

**Conflicto:** Bajo si el agente conoce dominios; alto si confunde deliverable con nota.

**Severidad:** Baja (mantener).

### 7. Checkpoints bloqueantes en daemon

| Perfil | Comportamiento | Ubicación |
|--------|----------------|-----------|
| `interactive` | Bloquea en `input()` | `core/user_checkpoint.py`, `console.py` |
| `headless` | Omite stall + exec_review | `user_checkpoint.py` ~L221–228 |
| `mvf_autonomous` | **No implementado** | — |

**Conflicto:** `headless` aún permite `needs_readaptation`, `attempt_cap_reached` → `input()` en daemon (`pulse_queue.py` sin `ask_user_fn`).

**Severidad:** Crítica para misiones nocturnas.

### 8. Roadmap validate reject → replan / abort

| Ubicación | `agent.py` `_run_vt_planning` |
|-----------|-------------------------------|
| Efecto | Roadmap rechazado puede impedir EXECUTE |

**Conflicto:** Planner JSON frágil bloquea misión simple que EXECUTE resolvería.

**Severidad:** Media.

### 9. MissionProgressTracker stall recovery

| Ubicación | `core/mission_progress.py`, checkpoints |
|-----------|------------------------------------------|
| Efecto | Nudges + checkpoint tras N turns sin progreso |

**Conflicto:** En daemon, stall checkpoint puede bloquear; en code_build largo, umbrales pueden ser agresivos.

**Severidad:** Media.

### 10. Deliverable guard (code_build)

| Ubicación | `agent.py`, write guards |
|-----------|-------------------------|
| Efecto | Exige artefactos antes de complete |

**Conflicto:** Positivo — alinear con MVF en lugar de duplicar lógica.

**Severidad:** Ninguna (conservar, migrar a MVF).

### 11. Sesión SQLite compartida (multi-proceso)

| Ubicación | `core/context.py` `clear_history`, `agent.py` `new_session` |
|-----------|--------------------------------------------------------------|
| Efecto | Orquestador crea sesión nueva; unlink `session.db` falla si `pulse` abierto |

**Conflicto:** WinError 32 + "Cannot operate on a closed database" — estado corrupto mid-mission.

**Severidad:** Alta para daemon + consola concurrentes.

**Fix planificado:** sesión aislada por job ([mvf_autonomous_plan.md](./mvf_autonomous_plan.md)); graceful skip si DB locked. Runbook: [MVF_AGENCY_ROADMAP.md](./MVF_AGENCY_ROADMAP.md) §5.2.

---

## Propuestas de relajación

### A. Gate MVF en MISSION_COMPLETE (implementar con R4)

```text
if mvf defined for session:
    if not mvf.validated:
        reject MISSION_COMPLETE + inject refine nudge
    else:
        allow (subject to safety guards only)
else:
    use legacy heuristics (relajadas, ver B)
```

**Archivos:** `agent.py`, nuevo `core/mvf_validator.py`.

### B. Umbrales dependientes de mission_kind

| mission_kind | MIN_TOOLS | MIN_SUBSTANTIVE | Notas |
|--------------|-----------|-----------------|-------|
| `code_build` | 2 | 1 | Sustantivo = write_file, run_script, delegate_to |
| `dev` | 2 | 1 | Igual |
| default (audit/recon) | 4 | 2 | Mantener actual |

Implementación sugerida: método `_completion_thresholds(mission_kind) -> tuple[int,int]` en `ReActAgent`.

### C. Perfil mvf_autonomous (R2)

- Todos los triggers excepto `PROMOTE_GATE` → notify-only, nunca bloquean.
- `headless` en daemon global → migrar a `mvf_autonomous` para jobs de cola.

### D. Tool-per-turn → "one substantive action"

- Prompt: "Emit one substantive action per turn (write, run, delegate, read for investigation). Multiple append_note allowed."
- Parser: mantener dedup; no rechazar batch si notes + 1 action.
- **Implementación parser/ST:** [tool_loop_plan.md](./tool_loop_plan.md) T1-B, T1-D (tras audit T-G1).

### E. LEAD delegate + append_note

- Permitir batch: `append_note` + `delegate_to` en mismo turn para LEAD.
- Actualizar `state/AGENTS.md` y validación de batch en parser.
- Detalle: [tool_loop_plan.md](./tool_loop_plan.md) T1-E.

### F. Roadmap validate — soft fail

- Si VALIDATE reject y `mission_kind == code_build`: log warning, continuar EXECUTE con fallback steps de `build_fallback_spec`.
- Config: `planner.roadmap_strict: false` para misiones autónomas.

### G. Mantener sin relajar

| Guard | Razón |
|-------|-------|
| WriteGuard paths destructivos | Seguridad |
| SANDBOX / HOST policy | Seguridad |
| crack_hash bootstrap guards | Anti-alucinación |
| Explicit STOP tokens | Canon §2.2 |
| PROMOTE_GATE post-MVF | Humano en merge |

---

## Orden de implementación

1. **R2** — `mvf_autonomous` (desbloquea daemon) → [mvf_autonomous_plan.md](./mvf_autonomous_plan.md)
2. **R4** — MVF validator + hook MISSION_COMPLETE → [mvf_validator_plan.md](./mvf_validator_plan.md)
3. **R1c / T1** (paralelo) — swap GPU + audit parser → [gpu_allocation_plan.md](./gpu_allocation_plan.md), [tool_loop_plan.md](./tool_loop_plan.md)
4. **Este plan §B, D, E, F** — relajaciones coordinadas con R4 + T1-A/D
5. **R7** — refine loop cuando MVF fail → [probe_test_refine_plan.md](./probe_test_refine_plan.md)

**No implementar §B sin §A** — evitar complete sin ningún gate.

---

## Criterios de aceptación

1. HelloGame puede completar con ≥2 tools sustantivos cuando MVF pasa.
2. HelloGame **no** puede completar si pytest falla (MVF gate).
3. `pulse_queue.py daemon` no espera stdin en jobs con `checkpoint_profile: mvf_autonomous`.
4. Misiones audit/recon mantienen umbrales conservadores o MVF explícito.
5. Documentación `state/AGENTS.md` actualizada si cambia regla LEAD batch.

---

## Tests planificados

| Test | Archivo |
|------|---------|
| code_build complete blocked without MVF | `tests/test_mvf_mission_complete.py` |
| code_build complete allowed with MVF pass | mismo |
| MIN_TOOLS lowered for code_build | `tests/test_completion_guards.py` (extender) |
| mvf_autonomous no block | `tests/test_checkpoint_mvf_autonomous.py` |

---

## Observaciones

1. La relajación de guards es **política de producto** — registrar decisiones en informe baseline ([agency_audit_plan.md](./agency_audit_plan.md)).
2. Optioneer (R3) no es prerequisito; PLAN actual basta para HelloGame.
3. Tras R4, considerar **deprecar** `MIN_TOOLS_BEFORE_COMPLETE` global en favor de MVF-only para misiones con spec.

---

## Referencias

- [AGENT_CANON.md](../AGENT_CANON.md) §2, §9
- [mvf_validator_plan.md](./mvf_validator_plan.md)
- [mvf_autonomous_plan.md](./mvf_autonomous_plan.md)
- [Generalization/multi_purpose_agent_design.md](./Generalization/multi_purpose_agent_design.md) — "advisory gates"
