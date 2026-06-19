# Plan: Perfil `mvf_autonomous` en checkpoints (R2)

> **Status:** IMPLEMENTED (config + código R2)  
> **Roadmap:** R2  
> **Prioridad:** P0  
> **Roadmap maestro:** [MVF_AGENCY_ROADMAP.md](./MVF_AGENCY_ROADMAP.md)  
> **Canon:** [AGENT_CANON.md](../AGENT_CANON.md) §2.4  
> **Depende de:** [agency_audit_plan.md](./agency_audit_plan.md)  
> **Desbloquea:** [hello_game_e2e_plan.md](./hello_game_e2e_plan.md), [pulse_queue_agency_plan.md](./pulse_queue_agency_plan.md), daemon nocturno estable

---

## Problema

La plantilla [`knowledge/queue_templates/hello_game.yaml`](../../knowledge/queue_templates/hello_game.yaml) declara `checkpoint_profile: mvf_autonomous`, y la columna existe en [`core/work_queue.py`](../../core/work_queue.py), pero:

1. [`core/orchestrator.py`](../../core/orchestrator.py) `_run_pwsh_mission` **no lee** el perfil del job.
2. [`core/user_checkpoint.py`](../../core/user_checkpoint.py) solo implementa `interactive` y `headless`.
3. `pulse_queue.py daemon` instancia agente sin `ask_user_fn` → checkpoints que llaman `input()` **cuelgan** el proceso.

Misiones nocturnas no pueden ejecutarse de forma autónoma según el canon goal lock.

---

## Objetivo

Implementar perfil **`mvf_autonomous`**: checkpoints informativos = notify-only; default `CONTINUE`; solo STOP explícito del operador detiene la misión. Excepción: `PROMOTE_GATE` post-MVF puede bloquear para aprobación humana.

---

## Comportamiento por perfil

| Perfil | Bloquea ejecución | Notify operador | STOP explícito |
|--------|-------------------|-----------------|----------------|
| `interactive` | Sí (todos triggers activos) | Inline en consola | Sí |
| `headless` | Parcial (sin stall/exec_review) | No | Sí si dispara otro trigger |
| **`mvf_autonomous`** | **No** (salvo PROMOTE_GATE) | **`operator_inbox.jsonl`** | **Sí** |

---

## Cambios planificados

### 1. `core/user_checkpoint.py`

#### 1.1 Nuevo trigger (opcional v1)

```python
class CheckpointTrigger(str, Enum):
    ...
    PROMOTE_GATE = "promote_gate"
```

#### 1.2 Perfil `mvf_autonomous` en `CheckpointGate.__init__`

```python
if self.profile == "mvf_autonomous":
    # Ningún trigger bloquea excepto promote (manejado aparte)
    self.active_triggers = frozenset()  # should_fire → False siempre
    self.notify_only = True
elif self.profile == "headless":
    ...
```

#### 1.3 `should_fire()`

- `mvf_autonomous`: retornar `False` para todos los triggers estándar.
- `PROMOTE_GATE`: retornar `True` solo si `promote_pending` flag en sesión (post-R4).

#### 1.4 `maybe_checkpoint()` — rama notify-only

```python
if getattr(self, "notify_only", False):
    append_operator_inbox(self.session_id, trigger, detail)
    return CheckpointDecision.CONTINUE
```

Helper nuevo:

```python
def append_operator_inbox(session_id: str, trigger: CheckpointTrigger, detail: str) -> None:
    path = app_root() / "state" / "sessions" / session_id / "operator_inbox.jsonl"
    entry = {"ts": iso_now(), "trigger": trigger.value, "detail": detail[:2000]}
    # append JSON line
```

#### 1.5 Ampliar `parse_user_decision()`

Añadir tokens canon §2.2:

| Input normalizado | Decision |
|-------------------|----------|
| `no`, `cancel`, `abort` | STOP |
| `no hagas`, `no hacer` | STOP |
| (existentes) `stop`, `terminar`, `x` | STOP |

#### 1.6 Lectura no bloqueante de inbox (daemon)

```python
async def read_operator_inbox_decision(session_id: str) -> CheckpointDecision | None:
    """Lee última línea con role=operator_reply si existe."""
```

Formato inbox extendido:

```json
{"ts": "...", "trigger": "needs_readaptation", "detail": "...", "source": "agent"}
{"ts": "...", "role": "operator_reply", "text": "continue"}
```

### 2. `core/orchestrator.py`

En `_run_pwsh_mission`, añadir parámetro `job: dict` o leer profile del contexto:

```python
async def _run_pwsh_mission(payload: dict, agent, job: dict | None = None) -> None:
    profile = (job or {}).get("checkpoint_profile") or "mvf_autonomous"
    job_id = (job or {}).get("id")

    # Apply checkpoint profile
    if agent is not None:
        agent.config.setdefault("checkpoint", {})["profile"] = profile
        agent._checkpoint_gate = CheckpointGate(agent.session_id, agent.config)
        agent._active_queue_job_id = job_id  # trazabilidad
```

Actualizar `execute_job` para pasar `job` completo a `_run_pwsh_mission`.

#### Daemon ask_user_fn stub

En `orchestrator_tick` cuando `agent is None`:

```python
async def _nonblocking_ask(message: str) -> str:
    return ""  # silence → CONTINUE bajo mvf_autonomous
```

Asignar a agente si expone hook pre-run, o configurar en `ReActAgent.run_mission`.

### 3. `agent.py` (mínimo)

- Aceptar `checkpoint_profile` opcional en `run_mission` kwargs o leer de `config` ya parcheado por orchestrator.
- Documentar que `default_ask_user` (input()) **no** debe usarse cuando profile es `mvf_autonomous`.
- **`new_session()` en jobs de cola:** no borrar `session.db` si otro proceso lo tiene abierto; preferir session id derivado de `job_id` (compat [MVF_AGENCY_ROADMAP.md](./MVF_AGENCY_ROADMAP.md) §5.2).

### 4. `config.yaml` (opcional)

```yaml
checkpoint:
  profile: headless  # global default consola interactiva
  # Jobs de cola override via checkpoint_profile column
```

---

## Tests planificados

Archivo: `tests/test_checkpoint_mvf_autonomous.py`

| Test | Assert |
|------|--------|
| `test_mvf_autonomous_should_fire_false` | stall trigger no dispara |
| `test_mvf_autonomous_maybe_checkpoint_returns_continue` | sin ask_user |
| `test_mvf_autonomous_writes_inbox` | archivo jsonl creado |
| `test_parse_stop_tokens_extended` | `cancel` → STOP |
| `test_orchestrator_applies_profile` | mock job con profile → config agent |

Extender `tests/test_work_queue.py`:

- Mock orchestrator + verificar `agent.config["checkpoint"]["profile"]`.

---

## Criterios de aceptación

1. Encolar `hello_game` + `py -3.10 pulse_queue.py run-once` **no espera stdin**.
2. Trigger simulado `needs_readaptation` escribe línea en `operator_inbox.jsonl`.
3. Operador puede append `{"role":"operator_reply","text":"stop"}` → próximo checkpoint parsea STOP (v2) o comando externo cancela job.
4. Consola interactiva con `checkpoint.profile: interactive` **sin cambios** en comportamiento.
5. `PROMOTE_GATE` documentado; implementación mínima puede stub notify-only hasta R4.

---

## Instrucciones de implementación (paso a paso)

1. Implementar `append_operator_inbox` + tests unitarios.
2. Añadir rama `mvf_autonomous` en `CheckpointGate`.
3. Wire orchestrator → agent config + pasar job dict.
4. Probar manualmente: daemon + template hello_game + run-once.
5. Actualizar [ORCHESTRATOR_CANON.md](../ORCHESTRATOR_CANON.md) § checkpoint_profile aplicado en runtime.

---

## Observaciones

1. **`headless` ≠ `mvf_autonomous`:** headless aún bloquea en triggers restantes; no usar como sustituto en cola.
2. **Promote gate:** canon dice silencio no revierte MVF; PROMOTE_GATE es notify + optional wait, no rollback.
3. **Consola vs daemon:** consola puede seguir `interactive`; cola nocturna debe default `mvf_autonomous`.

---

## Referencias

- [AGENT_CANON.md](../AGENT_CANON.md) §2.1, §2.4
- [loop_restrictions_review.md](./loop_restrictions_review.md) §7
- [mvf_validator_plan.md](./mvf_validator_plan.md) — PROMOTE_GATE post-MVF
