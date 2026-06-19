# Canon del orquestador — Pulse Queue

Cola unificada y runtime guiado para maximizar uso de las 3 GPUs sin interferir con el operador activo.

**Principio:** leemos caro (planificar/revisar), escribimos barato (ejecutar en ventanas idle), **promover humano** (solo en `promote` y ops destructivas).

Relacionado: [AGENT_CANON.md](AGENT_CANON.md) · [reference/DESIGN_SESSION_2026-06-19.md](reference/DESIGN_SESSION_2026-06-19.md)

---

## 1. Problema

Hoy hay **tres sistemas de cola separados**:

| Sistema | Store | Tipos |
|---------|-------|-------|
| `core/scheduler.py` | `.pulse/scheduler.db` | misiones pwsh_agent |
| `repo-hygiene/hygiene_daemon.py` | `.reports/review_queue.json` | ai_review, hub_scan |
| `core/hygiene_missions.py` | feed → scheduler | stubs `.mission` |

No hay prioridad global, ni ventanas idle/unificadas, ni visibilidad en una sola CLI.

---

## 2. Pulse Queue (cola unificada)

**DB:** `.pulse/work_queue.db`  
**Módulo:** `core/work_queue.py`

### Tipos de trabajo (`job_type`)

| job_type | Ejecutor | GPU preferida |
|----------|----------|---------------|
| `pwsh_mission` | `ReActAgent.run_mission` | coder (+ planner/intake vía pipeline) |
| `hygiene_review` | `ai_reviewer.py --repo` | planner + coder |
| `hygiene_scan` | `hub_scanner.py --repo` | CPU |
| `editorial` | stub → Hestia/subagent futuro | planner |
| `custom` | comando shell registrado | any |

### Campos de scheduling

```yaml
priority: 0-100          # mayor = antes
scheduled_at: ISO UTC    # no ejecutar antes de
cron_expr: "0 3 * * *"   # opcional, recurrente
requires_idle_seconds: 900
requires_night: {start: 22, end: 7}   # opcional
requires_gpu: intake|planner|coder|any
checkpoint_profile: mvf_autonomous|headless|interactive
```

### Payload JSON (por tipo)

**pwsh_mission:**
```json
{"mission_text": "...", "specialist": "workspace", "network_mode": "SANDBOX"}
```

**hygiene_review:**
```json
{"repo_path": "C:/.../pwsh_agent", "task_type": "ai_review", "target_file": null}
```

---

## 3. Runtime (`core/orchestrator.py`)

Ciclo cada `poll_interval_seconds` (default 15):

```text
1. Evaluar condiciones globales (idle Windows, ventana nocturna)
2. Importar stubs hygiene-feed → cola
3. pick_next() — mayor priority, due, condiciones OK, GPU no saturada
4. Ejecutar adapter del job_type
5. mark done/failed + next_run si cron
6. Emitir evento a consola si conectada
```

### Reglas de convivencia con operador

| Estado | Comportamiento |
|--------|----------------|
| Misión interactiva en curso | No lanzar jobs `requires_gpu=coder` |
| Idle < umbral y no noche | Solo jobs `priority >= 90` (urgentes) |
| Idle ≥ umbral o noche | Toda la cola elegible |
| Input activo (<10s) | Pausa aunque sea noche |

Reutiliza lógica de `repo-hygiene/scripts/hygiene_daemon.py` vía `core/idle_detect.py`.

---

## 4. CLI interactiva

### Entrada A — consola Pulse (`console.py`)

Comando **`queue`**: add / list / pause / resume / cancel / run-once / status / daemon

### Entrada B — standalone

```powershell
py -3.10 pulse_queue.py          # REPL interactivo
py -3.10 pulse_queue.py add      # wizard rápido
py -3.10 pulse_queue.py daemon   # solo orquestador (sin REPL agente)
py -3.10 pulse_queue.py run-once # un job si condiciones OK
```

### Wizard `add` (guiado)

1. Tipo: mission | hygiene review | hygiene scan | editorial
2. Objetivo / repo / path
3. Prioridad (default 50)
4. Cuándo: now | cron | idle-only | night-only | idle+night
5. Confirmación → `enqueue_job()`

---

## 5. Integración con roadmap del agente

Cada `pwsh_mission` en cola usa el pipeline canónico cuando esté implementado:

```text
ingest → optioneer → … → MVF validate → promote (humano)
```

Perfil default en cola nocturna: **`checkpoint_profile: mvf_autonomous`**.

Plantillas predefinidas en `knowledge/queue_templates/`:

| Template | job_type | payload |
|----------|----------|---------|
| `hello_game.yaml` | pwsh_mission | HelloGame E2E |
| `hygiene_pwsh_agent.yaml` | hygiene_review | repo pwsh_agent |
| `nightly_repos.yaml` | hygiene_review | lista repos |

---

## 6. Maximizar GPUs

```text
Cola priorizada
  ├─ priority 80+ hygiene_review  → GPU planner+coder (secuencial interno)
  ├─ priority 60 pwsh_mission     → pipeline 3 GPU
  └─ priority 40 editorial        → cuando idle largo

Paralelo futuro (R9):
  - Job A en coder EXECUTE + Job B hygiene planner phase
  - Requiere `OLLAMA_NUM_PARALLEL=2` y orchestrator aware de VRAM
```

---

## 7. Migración

| Legacy | Acción |
|--------|--------|
| `schedule` en console | Sigue funcionando; nuevos jobs → work_queue |
| `scheduler.db` | Lectura legacy; `schedule_mission()` delega a enqueue |
| hygiene_daemon | Opcional: alimentar misma work_queue o coexistir |

---

## 8. Archivos

| Path | Rol |
|------|-----|
| `core/work_queue.py` | CRUD cola SQLite |
| `core/orchestrator.py` | pick + execute + condiciones |
| `core/idle_detect.py` | idle Windows + night window |
| `pulse_queue.py` | CLI standalone |
| `console.py` | comando `queue` |
| `config.yaml` → `orchestrator:` | defaults globales |

---

## 9. Planes de implementación

Roadmap detallado (R2–R7, cola, HelloGame E2E, R1c GPU, T1 tooling): [plans/MVF_AGENCY_ROADMAP.md](plans/MVF_AGENCY_ROADMAP.md).

Índice: [plans/README.md](plans/README.md).

---

*Actualizar cuando se añadan job_types o reglas GPU.*
