# Plan: Pulse Queue — agencia y mutación de cola

> **Status:** PROPOSED  
> **Fase:** Q4 — Cola mutável  
> **Prioridad:** P1  
> **Roadmap maestro:** [MVF_AGENCY_ROADMAP.md](./MVF_AGENCY_ROADMAP.md)  
> **Canon:** [ORCHESTRATOR_CANON.md](../ORCHESTRATOR_CANON.md)  
> **Depende de:** [mvf_autonomous_plan.md](./mvf_autonomous_plan.md) (`_active_queue_job_id`)  
> **Desbloquea:** [probe_test_refine_plan.md](./probe_test_refine_plan.md) (notas), autonomía multi-componente

---

## Problema

[`core/work_queue.py`](../../core/work_queue.py) permite crear, listar, pausar y cancelar jobs, pero:

- No hay `get_job(job_id)` ni `update_job()`
- No hay notas append-only por job
- No hay subtasks / `parent_job_id`
- El agente no puede encolar follow-ups durante misión
- [`core/hygiene_missions.py`](../../core/hygiene_missions.py) alimenta **legacy** `scheduler.db`, no work_queue
- [`core/orchestrator.py`](../../core/orchestrator.py) ignora `requires_gpu` y no expone trazabilidad job↔session

Componentes distintos (hygiene feed, agente LEAD, CPU post-EVALUATE, operador CLI) no comparten un contrato de mutación de cola.

---

## Objetivo

Schema v2 + APIs de mutación + tool `queue_manage` para LEAD + migración hygiene → work_queue + CLI extendida.

---

## Schema v2 — migración SQLite

Añadir columnas a `work_jobs` (migration script o `ALTER TABLE` en `_connect`):

| Columna | Tipo | Descripción |
|---------|------|-------------|
| `notes_json` | TEXT | JSON array append-only |
| `parent_job_id` | TEXT | FK opcional a job padre |
| `updated_at` | TEXT | ISO UTC última mutación |
| `session_id` | TEXT | Sesión agente vinculada post-run |

Bump `schema_version` a `2`.

### Formato nota

```json
{
  "notes": [
    {"ts": "2026-06-19T12:00:00Z", "source": "agent", "text": "MVF attempt 2: pytest import error"},
    {"ts": "2026-06-19T12:05:00Z", "source": "operator", "text": "retry with workspace specialist"}
  ]
}
```

---

## Nuevas APIs — `core/work_queue.py`

```python
def get_job(job_id: str) -> dict | None: ...

def update_job(
    job_id: str,
    *,
    title: str | None = None,
    priority: int | None = None,
    payload: dict | None = None,
    status: str | None = None,
    next_run_at: str | None = None,
    session_id: str | None = None,
) -> bool: ...

def append_job_note(job_id: str, text: str, *, source: str = "agent") -> bool: ...

def enqueue_subtask(
    parent_job_id: str,
    job_type: str,
    payload: dict,
    **kwargs,
) -> str:
    """Enqueue child with parent_job_id set; inherits night/idle gates optional."""
```

### Reglas de mutación

| Acción | Quién | Restricción |
|--------|-------|-------------|
| `append_job_note` | agent, operator, hygiene | Max 500 chars/nota; max 200 notas/job |
| `update_job` payload | agent LEAD | No cambiar `job_type` |
| `pause`/`cancel` | operator | Agent solo pause self job |
| `enqueue_subtask` | agent LEAD | Max depth 2 (parent → child) |

---

## Tool agente — `queue_manage`

Nuevo módulo [`tools/queue_ops.py`](../../tools/queue_ops.py) (o extensión tools registry):

```python
def queue_manage(
    action: str,  # enqueue | note | update | pause | status
    job_id: str | None = None,
    mission_text: str | None = None,
    note: str | None = None,
    priority: int = 50,
) -> dict:
```

### Registro

- Disponible solo para specialist `lead` (o flag `allow_queue_tools`)
- Documentación: `knowledge/tools/queue_manage.md`
- SANDBOX: permitido (solo SQLite local)

### Acciones v1

| action | Params | Efecto |
|--------|--------|--------|
| `enqueue` | mission_text, priority | `enqueue_job("pwsh_mission", ...)` |
| `note` | job_id, note | `append_job_note` |
| `update` | job_id, mission_text | merge payload |
| `status` | job_id optional | `get_job` o `queue_stats` |
| `pause` | job_id | solo si `_active_queue_job_id == job_id` |

---

## Integración agente

En `agent.py`:

- Tras `run_mission` start desde orchestrator: `self._active_queue_job_id = job_id`
- En `_final_synthesis` / MVF pass: `append_job_note(job_id, "MVF validated", source="agent")`
- Exponer `queue_manage` en tool list para LEAD

---

## Migraciones

### 1. Hygiene feed → work_queue

[`core/hygiene_missions.py`](../../core/hygiene_missions.py):

```python
# Reemplazar schedule_mission(...) por:
enqueue_job(
    "pwsh_mission",
    {"mission_text": objective, "specialist": specialist, "network_mode": "SANDBOX"},
    title=f"hygiene: {objective[:60]}",
    priority=55,
)
```

### 2. Legacy scheduler bridge

[`core/scheduler.py`](../../core/scheduler.py) `schedule_mission()`:

```python
def schedule_mission(...):
    from core.work_queue import enqueue_job
    return enqueue_job("pwsh_mission", {...}, ...)
```

Mantener lectura de `scheduler.db` en sweep solo si `run_legacy_scheduler: true` y jobs pendientes.

### 3. Orchestrator loop

[`core/orchestrator.py`](../../core/orchestrator.py) `orchestrator_loop`:

- Poll `poll_hygiene_missions()` al inicio de tick (como sweep_loop)
- Tras run: `update_job(job_id, session_id=agent.session_id)`

### 4. GPU field (stub v1)

`pick_next_job`: log warning si `requires_gpu != any` y no hay dispatcher VRAM — no bloquear aún (R9).

---

## CLI extendida

### `pulse_queue.py`

| Comando | Acción |
|---------|--------|
| `show <id>` | `get_job` + pretty print payload + notes |
| `note <id>` | prompt texto → `append_job_note` |
| `template <name>` | `enqueue_from_template` — **preferir sobre wizard `add`** ([MVF_AGENCY_ROADMAP.md](./MVF_AGENCY_ROADMAP.md) §4.1) |

### `console.py` → `queue`

Añadir choices: `show`, `note`

---

## Tests planificados

| Test | Archivo |
|------|---------|
| schema v2 migration | `tests/test_work_queue.py` |
| get/update/note | mismo |
| enqueue_subtask parent link | mismo |
| queue_manage tool enqueue | `tests/test_queue_ops.py` |
| hygiene poll → work_queue | `tests/test_hygiene_missions.py` (update) |

---

## Criterios de aceptación

1. Operador puede `pulse_queue.py show <id>` y ver notas.
2. Agente LEAD puede `queue_manage(action="note", ...)` durante misión.
3. Hygiene stub `.mission` aparece en `work_queue.db`, no solo `scheduler.db`.
4. `schedule_mission` desde consola crea row en work_queue.
5. Migración schema no rompe jobs existentes.

---

## Instrucciones de implementación

1. Schema migration + APIs en `work_queue.py` + tests.
2. CLI show/note.
3. `queue_ops.py` + tool registration + skill md.
4. Wire agent `_active_queue_job_id` + notes en cierre MVF.
5. Migrar hygiene_missions + scheduler bridge.
6. Mover hygiene poll a orchestrator_loop.
7. Actualizar [ORCHESTRATOR_CANON.md](../ORCHESTRATOR_CANON.md) § migración.

---

## Observaciones

1. **Dos DBs coexisten** hasta migración completa — documentar en README operador.
2. **Cancelar jobs ajenos** — requiere confirmación humana; agent no cancel arbitrary.
3. **Subtasks v2** — UI tree en `pulse_queue list --tree`.
4. **R9 paralelismo GPU** — `requires_gpu` enforcement fuera de scope.

---

## Referencias

- [ORCHESTRATOR_CANON.md](../ORCHESTRATOR_CANON.md)
- [probe_test_refine_plan.md](./probe_test_refine_plan.md)
- [mvf_autonomous_plan.md](./mvf_autonomous_plan.md)
- [hello_game_e2e_plan.md](./hello_game_e2e_plan.md)
