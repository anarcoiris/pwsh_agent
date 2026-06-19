# Plan: MVF validator y `mvf.json` (R4)

> **Status:** IMPLEMENTED (R4 + R4b exit gate)  
> **Roadmap:** R4  
> **Prioridad:** P1  
> **Roadmap maestro:** [MVF_AGENCY_ROADMAP.md](./MVF_AGENCY_ROADMAP.md)  
> **Canon:** [AGENT_CANON.md](../AGENT_CANON.md) §2.3, §9  
> **Depende de:** [loop_restrictions_review.md](./loop_restrictions_review.md)  
> **Desbloquea:** [probe_test_refine_plan.md](./probe_test_refine_plan.md), [hello_game_e2e_plan.md](./hello_game_e2e_plan.md)

---

## Problema

Hoy la misión termina cuando el LLM declara `MISSION_COMPLETE` y pasan heurísticas (`MIN_TOOLS_BEFORE_COMPLETE`, `MissionProgressTracker`). No hay:

- Archivo `mvf.json` por sesión
- Validador CPU de checks deterministas (`file_exists`, `command`)
- Gate que impida complete sin pytest verde

El canon define MVF como **conjunto mínimo verificable** — la implementación debe hacerlo auditable en disco.

---

## Objetivo

Nuevo módulo `core/mvf_validator.py` + persistencia `state/sessions/<id>/mvf.json` + hook en `run_mission` antes de aceptar `MISSION_COMPLETE` + orchestrator solo marca job done si MVF validado (o job sin MVF).

---

## Schema `mvf.json`

```json
{
  "deliverables": ["HelloGame/PLAN.md", "HelloGame/game.py", "HelloGame/tests/test_game.py"],
  "checks": [
    {"type": "file_exists", "path": "HelloGame/game.py"},
    {"type": "file_exists", "path": "HelloGame/PLAN.md"},
    {"type": "command", "cmd": "py -3.10 -m pytest HelloGame/tests -q", "exit_code": 0, "cwd": null}
  ],
  "validated": false,
  "last_run_at": null,
  "last_results": [],
  "derived_from": "intent_spec"
}
```

### Check types v1

| type | Campos | Comportamiento |
|------|--------|----------------|
| `file_exists` | `path` (relativo a repo root) | `Path.exists()` && is_file |
| `command` | `cmd`, `exit_code`, `cwd?` | `subprocess.run`, shell=False, split cmd si lista |

### Resultado por check

```json
{"type": "command", "path": null, "ok": false, "detail": "exit 1: ...", "duration_ms": 1200}
```

---

## API `core/mvf_validator.py`

```python
@dataclass
class MvfCheckResult:
    type: str
    ok: bool
    detail: str = ""
    path: str | None = None

@dataclass
class MvfResult:
    validated: bool
    checks: list[MvfCheckResult]

def mvf_path(session_id: str) -> Path: ...

def load_mvf(session_id: str) -> dict | None: ...

def save_mvf(session_id: str, data: dict) -> None: ...

def derive_mvf_from_intent(spec: IntentSpec, mission_text: str) -> dict:
    """Build mvf from deliverables + heuristics (code_build → pytest)."""

def run_checks(checks: list[dict], root: Path | None = None) -> MvfResult: ...

def validate_session(session_id: str, *, persist: bool = True) -> MvfResult: ...
```

### Derivación automática (v1)

Reglas para `mission_kind == code_build`:

1. `deliverables` ← `IntentSpec.deliverables` o regex desde mission_text
2. Por cada `.py` deliverable en paquete, añadir check `file_exists`
3. Si mission_text menciona `pytest` o `tests/`, añadir check command pytest
4. Plantilla YAML puede incluir bloque `mvf` override (ver hello_game)
5. Paths deliverables: **`HelloGame/`** PascalCase — ver [MVF_AGENCY_ROADMAP.md](./MVF_AGENCY_ROADMAP.md) §4.1

---

## Integración

### 1. `core/intent_spec.py`

- Campo opcional `mvf: dict | None` en `IntentSpec`
- Tras `_compute_intent_spec` exitoso en `agent.py`:

```python
from core.mvf_validator import derive_mvf_from_intent, save_mvf
if self._intent_spec and not load_mvf(self.session_id):
    mvf = derive_mvf_from_intent(self._intent_spec, user_prompt)
    save_mvf(self.session_id, mvf)
```

### 2. `agent.py` — gate MISSION_COMPLETE

En bloque ~L2424, **antes** de aceptar complete:

```python
from core.mvf_validator import load_mvf, validate_session

mvf_data = load_mvf(self.session_id)
if mvf_data and mvf_data.get("checks"):
    mvf_result = validate_session(self.session_id)
    if not mvf_result.validated:
        self._add_nudge(
            f"[SYSTEM] MISSION_COMPLETE rejected — MVF not validated. "
            f"Failed: {[c.detail for c in mvf_result.checks if not c.ok]}"
        )
        continue  # stay in loop
```

Si no hay `mvf.json` → fallback heurístico actual ([loop_restrictions_review.md](./loop_restrictions_review.md) §B).

### 2b. R4b — gate en **todas** las rutas de salida

Además de `MISSION_COMPLETE` en el loop, cualquier cierre exitoso debe pasar por `mvf_exit_blocked()`:

| Ruta | Antes | Después |
|------|-------|---------|
| `MISSION_COMPLETE` tool | heurístico | `mvf_exit_blocked` → `_complete_mission_success` o nudge |
| `max_steps` / budget | texto fijo | `_mission_exit_failure_text` o gate si claims success |
| checkpoint `mvf_autonomous` | bypass | mismo gate |
| `_final_synthesis` handoff | podía completar | `_complete_mission_success` |

Helpers en `agent.py`:

- `_complete_mission_success()` — único punto que emite `MISSION_COMPLETED`
- `_mission_exit_failure_text(reason)` — salida fallida consistente
- `_hygiene_context_allowed()` — sin RAG hygiene en `code_build` puro

Checks extendidos: `dir_exists`, `dir_count` (bulk missions tipo `ejemplos-texto/`).

Post-`append_note`: `core/delivery_probe.probe_append_note_line()` advierte si el disco no coincide con claims.

### 3. `core/orchestrator.py`

Tras `run_mission` retorna OK:

```python
from core.mvf_validator import load_mvf, validate_session
mvf = load_mvf(agent.session_id)
if mvf and mvf.get("checks"):
    if not validate_session(agent.session_id).validated:
        raise RuntimeError("MVF validation failed post-mission")
mark_job_completed(job_id)
```

### 4. Plantilla HelloGame

Añadir en [`knowledge/queue_templates/hello_game.yaml`](../../knowledge/queue_templates/hello_game.yaml):

```yaml
mvf:
  deliverables:
    - HelloGame/PLAN.md
    - HelloGame/game.py
    - HelloGame/tests/test_game.py
  checks:
    - type: file_exists
      path: HelloGame/game.py
    - type: command
      cmd: "py -3.10 -m pytest HelloGame/tests -q"
      exit_code: 0
```

`enqueue_from_template` puede copiar bloque `mvf` al payload; `derive` lo lee si presente.

---

## Config

`config.yaml`:

```yaml
mvf:
  enabled: true
  auto_derive: true
  max_refine_attempts: 5  # usado por R7
```

---

## Tests planificados

Archivo: `tests/test_mvf_validator.py` (sin LLM, tmp_path)

| Test | Descripción |
|------|-------------|
| `test_file_exists_pass_fail` | crea/borra archivo |
| `test_command_check_pytest` | mini proyecto con test |
| `test_derive_code_build` | IntentSpec → checks |
| `test_validate_session_persists` | validated flag en json |
| `test_mission_complete_blocked_without_mvf` | mock agent hook |

---

## Criterios de aceptación

1. Tras misión HelloGame exitosa, `mvf.json` tiene `validated: true`.
2. `MISSION_COMPLETE` rechazado si pytest falla (aunque LLM declare complete).
3. Job en cola queda `pending` + `last_error` si MVF fail post-run.
4. Sesiones sin MVF (chat casual) no afectadas.
5. Checks son deterministas y repetibles sin LLM.

---

## Instrucciones de implementación

1. Crear `core/mvf_validator.py` + tests puros CPU.
2. Wire derive + save tras INTAKE en `run_mission`.
3. Añadir gate en MISSION_COMPLETE.
4. Wire orchestrator post-mission validation.
5. Extender plantilla hello_game + `queue_templates.py` si hace falta pasar mvf block.
6. Coordinar con [loop_restrictions_review.md](./loop_restrictions_review.md) — relajar MIN_TOOLS cuando MVF activo.

---

## Observaciones

1. **Roadmap validate ≠ MVF validate** — mantener ambos; roadmap pre-EXECUTE, MVF post-EXECUTE.
2. **Paths relativos** — resolver contra `app_root()` de [`core/runtime_paths.py`](../../core/runtime_paths.py).
3. **Windows commands** — usar `py -3.10` explícito como en canon HelloGame.
4. **Promote gate** — MVF validated habilita transición a PROMOTE_GATE (R2); no revertir en silencio.

---

## Referencias

- [AGENT_CANON.md](../AGENT_CANON.md) §2.3, §14
- [probe_test_refine_plan.md](./probe_test_refine_plan.md)
- [hello_game_e2e_plan.md](./hello_game_e2e_plan.md)
