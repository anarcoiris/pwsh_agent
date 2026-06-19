# Plan: HelloGame E2E — vertical slice (R5)

> **Status:** PROPOSED  
> **Roadmap:** R5  
> **Prioridad:** P2  
> **Roadmap maestro:** [MVF_AGENCY_ROADMAP.md](./MVF_AGENCY_ROADMAP.md)  
> **Canon:** [AGENT_CANON.md](../AGENT_CANON.md) §14  
> **Depende de:** R2, R4, [loop_restrictions_review.md](./loop_restrictions_review.md); R7 recomendado; **R1c** ([gpu_allocation_plan.md](./gpu_allocation_plan.md)) y **T1** ([tool_loop_plan.md](./tool_loop_plan.md)) recomendados antes de E2E live  
> **Desbloquea:** Demo funcional, R6 pipelines YAML

---

## Problema

HelloGame es el **vertical slice de referencia** del canon (§14) pero hoy:

- No existe `HelloGame/` con artefactos en repo
- `tools_dev/vertical_smoke.py` para en INTAKE→PLAN→VALIDATE roadmap
- `tools_dev/night_mission.py` **no existe**
- Plantilla [`knowledge/queue_templates/hello_game.yaml`](../../knowledge/queue_templates/hello_game.yaml) encola misión pero nada asserta éxito
- Tests unitarios mockeados (`tests/test_hello_game_mission.py`) no cubren E2E live

---

## Objetivo

Demostración end-to-end: encolar HelloGame → ejecutar vía orquestador → artefactos en disco → pytest verde → `mvf.json` validated → job `done`.

---

## Definición de done (MVF)

### Artefactos

```
HelloGame/
  PLAN.md           # diseño mínimo del juego ASCII
  game.py           # juego ejecutable (stdin loop o similar)
  tests/
    test_game.py    # smoke import + assert básico
```

### Checks deterministas

```json
{
  "deliverables": [
    "HelloGame/PLAN.md",
    "HelloGame/game.py",
    "HelloGame/tests/test_game.py"
  ],
  "checks": [
    {"type": "file_exists", "path": "HelloGame/PLAN.md"},
    {"type": "file_exists", "path": "HelloGame/game.py"},
    {"type": "file_exists", "path": "HelloGame/tests/test_game.py"},
    {"type": "command", "cmd": "py -3.10 -m pytest HelloGame/tests -q", "exit_code": 0}
  ],
  "validated": true
}
```

### Cola

- Job from template `hello_game` → `status=done`, `last_error=NULL`
- `checkpoint_profile: mvf_autonomous` aplicado (R2)

---

## Pre-requisitos operativos

| Requisito | Verificación |
|-----------|--------------|
| Ollama multi-GPU | `Ollama/docker/verify-multi-gpu.ps1` |
| **R1c swap 1070** | INTAKE :11435, EXECUTE :11436 — [gpu_allocation_plan.md](./gpu_allocation_plan.md) Decisión A |
| Endpoints config | `config.yaml` intake/planner/coder URLs (post-swap) |
| **T1 audit** | `llm_audit.jsonl`: ≤50% turns EXECUTE solo-ST — [tool_loop_plan.md](./tool_loop_plan.md) |
| Sin hygiene contention | No `ai_reviewer` parallel |
| R2 implementado | daemon no bloquea stdin |
| R4 implementado | mvf gate activo |
| MIN_TOOLS relajado | [loop_restrictions_review.md](./loop_restrictions_review.md) §B |

Referencia hardware: [memory/2026-06-19.md](../../memory/2026-06-19.md)

---

## Artefactos a crear

### 1. `tools_dev/night_mission.py`

Script CLI one-shot para E2E nocturno.

```powershell
py -3.10 tools_dev/night_mission.py --template hello_game --force --assert-mvf
```

#### Args

| Flag | Descripción |
|------|-------------|
| `--template NAME` | Stem YAML en `knowledge/queue_templates/` (default: hello_game) |
| `--force` | `orchestrator_tick(force=True)` bypass idle/night |
| `--assert-mvf` | Exit 1 si mvf.json missing o validated=false |
| `--assert-pytest` | Run pytest independiente post-mission |
| `--timeout SEC` | Max wait mission (default 3600) |
| `--dry-run` | Solo enqueue, no execute |

#### Flujo

```text
1. enqueue_from_template(template)
2. asyncio.run(orchestrator_tick(agent=None, force=force))
3. Resolve session_id from agent / active_session / job.session_id
4. load_mvf(session_id) → assert validated (if --assert-mvf)
5. subprocess pytest HelloGame/tests (if --assert-pytest)
6. print summary JSON to stdout
7. exit 0 / 1
```

### 2. Actualizar plantilla hello_game

Añadir bloque `mvf` (ver [mvf_validator_plan.md](./mvf_validator_plan.md)) y opcional `mvf` en payload para derive.

### 3. Test integración (opcional)

`tests/test_hello_game_e2e.py`:

```python
@pytest.mark.live
@pytest.mark.skipif(not ollama_available(), reason="no ollama")
def test_hello_game_night_mission():
    ...
```

Default CI: skip live; run en nightly workflow manual.

### 4. Documentación operador

Sección en este plan + entrada en [agency_audit_plan.md](./agency_audit_plan.md) baseline runs.

---

## Flujo canon §14 mapeado

| Paso canon | Implementación R5 |
|------------|-------------------|
| 1. ingest | IntentSpec code_build via run_mission INTAKE |
| 2. index | ⚠️ skip v1 (workspace vacío OK) |
| 3. optioneer | ⚠️ skip (R3 deferred) — PLAN elige estrategia |
| 4. draft | EXECUTE write_file game.py, PLAN.md, test |
| 5. validate | mvf_validator pytest |
| 6. mvf.validated | true en mvf.json |
| 7. promote | notify operator_inbox; silencio OK |

---

## Procedimiento de prueba manual

### A. Smoke rápido (sin EXECUTE)

```powershell
py -3.10 tools_dev\vertical_smoke.py
```

### B. E2E forzado

```powershell
# Terminal 1 — optional daemon
py -3.10 pulse_queue.py daemon

# Terminal 2 — one shot
py -3.10 tools_dev\night_mission.py --template hello_game --force --assert-mvf --assert-pytest
```

### C. Verificación post-run

```powershell
dir HelloGame
py -3.10 -m pytest HelloGame\tests -q
py -3.10 pulse_queue.py list  # job done
type state\sessions\<id>\mvf.json
```

### D. Inspección agencia

Completar checklist en [agency_audit_plan.md](./agency_audit_plan.md) § R+P+A+V+I.

---

## Criterios de aceptación R5

1. `HelloGame/game.py`, `PLAN.md`, `tests/test_game.py` existen tras night_mission.
2. `py -3.10 -m pytest HelloGame/tests -q` exit 0.
3. `mvf.json` → `"validated": true`.
4. Job cola → `status=done`.
5. `night_mission.py --assert-mvf` exit 0 en entorno GPU OK.
6. Sin bloqueo stdin durante ejecución daemon/run-once.

---

## Criterios de fallo / debug

| Síntoma | Causa probable | Acción |
|---------|----------------|--------|
| Job pending forever | idle/night gate | usar `--force` |
| Daemon hung | checkpoint interactive | verificar R2 profile |
| MISSION_COMPLETE rejected | MIN_TOOLS / MVF | loop_restrictions + R4 logs |
| pytest fail | código incompleto | R7 refine loop |
| Ollama timeout | GPU contention | stop hygiene jobs |
| Empty HelloGame | mission never ran | check last_error en cola |
| Wrong paths (`hello_game/greet.js`) | wizard text libre, no plantilla | usar `enqueue_from_template("hello_game")` |
| SQLite WinError 32 | pulse + daemon concurrentes | [MVF_AGENCY_ROADMAP.md](./MVF_AGENCY_ROADMAP.md) §5.2 |
| delegate_to failed | specialist workspace sin LEAD | ver §4.5 roadmap maestro |

---

## Instrucciones de implementación

1. Completar R2 + R4 (+ relajación guards).
2. Extender hello_game.yaml con mvf block.
3. Implementar `night_mission.py`.
4. Correr manual E2E; documentar baseline en agency_audit_plan.
5. Añadir test `@pytest.mark.live` opcional.
6. Marcar R5 done en [DESIGN_SESSION_2026-06-19.md](../reference/DESIGN_SESSION_2026-06-19.md) cuando pase.

---

## Observaciones

1. **No commitear HelloGame/** si es output efímero de misión — decidir `.gitignore` vs golden fixture; recomendación: gitignore + assert en CI live only.
2. **Duración** — primera corrida cold Ollama ~minutos; timeout generoso.
3. **workspace specialist** — plantilla usa `specialist: workspace`; para code_build con delegate, considerar LEAD en payload o relajar en [loop_restrictions_review.md](./loop_restrictions_review.md).
4. **R6 pipeline YAML** — post-R5; extraer pasos HelloGame a `knowledge/pipelines/code_build.yaml`.
5. **Paths PascalCase** — MVF checks deben apuntar a `HelloGame/`, no `hello_game/` ([MVF_AGENCY_ROADMAP.md](./MVF_AGENCY_ROADMAP.md) §4.1).

---

## Referencias

- [AGENT_CANON.md](../AGENT_CANON.md) §14
- [knowledge/queue_templates/hello_game.yaml](../../knowledge/queue_templates/hello_game.yaml)
- [agency_audit_plan.md](./agency_audit_plan.md)
- [mvf_validator_plan.md](./mvf_validator_plan.md)
- [mvf_autonomous_plan.md](./mvf_autonomous_plan.md)
