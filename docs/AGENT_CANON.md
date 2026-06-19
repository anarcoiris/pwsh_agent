# Canon del agente local multi-modelo

Documento normativo que unifica: pipeline vertical (`exploration-kernel`), despliegue multi-GPU, principios de diseño (`docs/knowledge.md`), cooperación con repo-hygiene/editorial, patrones Hestia (`Hestia-main`), y reglas de interacción humana.

**Versión:** 2026-06-19  
**Hardware de referencia:** 1× GTX 1080 + 2× GTX 1070 (Pascal 8 GB, sin NVLink)  
**Principio rector (local):** **Leemos caro, escribimos barato. Promover humano.**

> En cloud/editorial el kernel usa la variante invertida (*leer barato, escribir caro*). En Pascal local, **comprensión profunda en 7B/VT** y **salida acotada en coder** es la asignación correcta.

---

## 1. Principios no negociables

| # | Principio | Implicación |
|---|-----------|-------------|
| P1 | **Contexto ≠ memoria** | Estado en CPU (grafo, SQLite, RAG); al LLM solo subtarea + fragmento JSON |
| P2 | **Handoffs JSON, razonamiento libre** | Prosa solo dentro de una inferencia; contratos JSON entre fases |
| P3 | **Paralelismo por especialización** | Un modelo completo por GPU; no model-parallel en Pascal |
| P4 | **System prompt ≠ orquestador** | Invariantes en prompt; FSM y transiciones en código |
| P5 | **Tests como verdad** | MVF = artefactos que pasan verificación determinista |
| P6 | **Goal lock → MVF-first** | Tras fijar objetivo, el bucle no se rompe por silencio ni sugerencias de aclaración |
| P7 | **Promover humano** | La promoción a canon/merge es humana; la ejecución hacia MVF es autónoma |
| P8 | **Trazabilidad** | Manifest append-only por fase; artifacts auditable (estilo Hestia) |

---

## 2. Regla Goal Lock y MVF (interacción humana)

Una vez que **INTAKE** produce un `IntentSpec` con objetivo aceptado (`shadow_mode: false`, goal no vacío):

### 2.1 Qué NO debe bloquear el bucle

- Silencio del operador
- Ausencia de respuesta a sugerencias de aclaración
- Preguntas del agente sobre acotaciones, calidades o refinamiento del scope
- Checkpoints informativos (`exec_result_review`, progreso parcial)
- Incertidumbre declarada en `unknowns[]` del spec

**Interpretación:** ofrecer interacción ≠ exigir interacción. El agente puede *promover* conversación (log, banner, cola `promote_pending`) pero **sigue ejecutando** hacia el MVF.

### 2.2 Qué SÍ detiene o replantea

| Señal | Efecto |
|-------|--------|
| Negación explícita: `stop`, `no`, `cancel`, `abort`, `terminar`, `no hagas…` | `CheckpointDecision.STOP` |
| Operación destructiva no whitelisted | Bloqueo de herramienta (WriteGuard, policy) |
| MVF validado + fase `promote` | Pausa para aprobación humana (canon/merge) |
| Riesgo `safety.destructive` sin confirmación | Bloqueo puntual, no aborto de misión |

### 2.3 Definición MVF (Minimum Viable / Functional)

Conjunto mínimo verificable definido en `IntentSpec.success_criteria` + tests/checks explícitos:

```json
{
  "mvf": {
    "deliverables": ["HelloGame/game.py", "HelloGame/PLAN.md"],
    "checks": [
      {"type": "file_exists", "path": "HelloGame/game.py"},
      {"type": "command", "cmd": "py -3.10 -m pytest HelloGame/tests -q", "exit_code": 0}
    ],
    "validated": false
  }
}
```

El bucle autónomo termina cuando `mvf.validated === true` o el operador niega explícitamente.

### 2.4 Perfiles de checkpoint

| Perfil | Uso | Comportamiento |
|--------|-----|----------------|
| `interactive` | Consola con operador presente | Checkpoints bloqueantes clásicos |
| `headless` | Harness, CI, misiones nocturnas | Omite stall/exec_review bloqueantes |
| **`mvf_autonomous`** | **Default tras goal lock** | Checkpoints = **notify-only**; default `CONTINUE`; solo STOP explícito |

Implementación objetivo: extender `core/user_checkpoint.py` con perfil `mvf_autonomous` y cola `state/sessions/<id>/operator_inbox.jsonl` para sugerencias no bloqueantes.

---

## 3. Nomenclatura formal

| Nombre canónico | Tag Ollama | GPU | Fases vertical |
|-----------------|------------|-----|----------------|
| **Intake Agent** | `chat-analyzer` | 1070 #2 | `ingest`, `index` (ligero) |
| **Planner** | `vibethinker:3b` | 1080 | `optioneer`, `refine` |
| **Execution Agent** | `qwen2.5-coder:7b-instruct` | 1070 #1 | `draft`, `validate` (sintaxis) |
| **CPU State Manager** | — | CPU | grafo, FSM, tests, RAG, memoria |
| **Validation** (determinista) | — | CPU | pytest, lint, `file_exists` |
| **Promote Gate** | — | humano | `promote` |

Alias legacy: `chat-analyzer` se mantiene como tag Docker; en docs usar **Intake Agent**.

---

## 4. Pipeline vertical canónico (10 fases)

Fuente: `exploration-kernel/protocols/vertical_pipeline.md`

```text
Usuario
  │
  ▼ ingest ──────────── Intake Agent (JSON IntentSpec)
  ▼ index ───────────── CPU + RAG (digests, no prose al LLM)
  ▼ analyze_unit ────── digests por archivo/módulo
  ▼ analyze_cross ───── grafo dependencias (CPU)
  ▼ triage ──────────── cola priorizada (CPU)
  ▼ optioneer ───────── Planner: OptionsPack A/B/C  ◄── FASE NUEVA
  ▼ [promote scope] ─── humano solo si needs_human (no bloquea MVF)
  ▼ draft ───────────── Execution: snippets, patches, tools
  ▼ refine ──────────── Planner: delta tras fallo/test
  ▼ validate ────────── tests + checks deterministas
  ▼ promote ──────────── humano: canon, merge, cierre
  ▼
Respuesta + experiencia embed (CPU)
```

### 4.1 Mapeo pwsh_agent actual → objetivo

| Fase canónica | Implementado | Gap |
|---------------|--------------|-----|
| `ingest` | ✅ `IntentFormalizer` | — |
| `index` | ⚠️ RAG parcial, hygiene feed | index explícito pre-plan |
| `analyze_unit/cross` | ❌ | digests JSON por repo |
| `triage` | ❌ | cola P0–P3 / prioridad |
| **`optioneer`** | ❌ (colapsado en PLAN) | **`OptionsPack` A/B/C** |
| `draft` | ✅ ReAct EXECUTE | subtarea acotada |
| `refine` | ✅ EVALUATE | acoplar a fallo de test |
| `validate` | ⚠️ roadmap + parcial | MVF test suite |
| `promote` | ⚠️ checkpoint | gate formal post-MVF |

### 4.2 Pipeline runtime actual (5 fases)

```
INTAKE → PLAN → VALIDATE → EXECUTE → EVALUATE
```

Evolución: insertar **index → optioneer** entre INTAKE y PLAN; separar **validate MVF** de **validate roadmap**.

---

## 5. Optioneer

**No es planificar pasos.** Es **elegir estrategia** antes de generación cara.

### Contrato `OptionsPack`

```json
{
  "problem_frame": "string",
  "unknowns": ["string"],
  "options": [
    {
      "id": "A",
      "strategy": "string",
      "pros": ["string"],
      "cons": ["string"],
      "syntax_sketch": "string",
      "tests_first": ["string"],
      "confidence": 0.0
    }
  ],
  "recommendation": "A",
  "needs_human": false
}
```

### Flujo

1. Input: `IntentSpec` compact + index digest (no monólogo previo)
2. Planner razona (prosa interna); serializa `OptionsPack`
3. Coder ejecuta **micro-probes** paralelos (≤10 líneas, sintaxis/import)
4. CPU elige `recommendation` si `!needs_human`; si `needs_human` → **notify** operador, **no bloquear** salvo negación explícita
5. Solo la opción elegida alimenta `TaskGraph` / roadmap

---

## 6. Arquitectura de cuatro capas

| Capa | Artefacto | Contenido |
|------|-----------|-----------|
| Política | System prompt / SOUL | Rol, límites, formato |
| Orquestador | `agent.py` + FSM | Estado, transiciones, goal lock |
| Skill activa | Llamada por fase | Intake / Optioneer / Draft / Refine |
| Contrato | JSON schema | IntentSpec, OptionsPack, TaskGraph, MVF, manifest |

**VT 3B** es router + descompositor con contexto, **no** oráculo de planificación profunda (Hallazgo 4, `knowledge.md`).

---

## 7. Multi-GPU y contexto

| Contenedor | Puerto | Modelo | num_ctx aplicado |
|------------|--------|--------|------------------|
| ollama-intake | 11436 | chat-analyzer | 8192 |
| ollama-planner | 11434 | vibethinker:3b | 32768 |
| ollama-coder | 11435 | qwen-coder:7b | 16384 |

- `OLLAMA_KV_CACHE_TYPE=q4_0`; probe: `Ollama/docker/probe-vram-context.ps1`
- `unload_after_call: false` — modelos residentes
- Concurrencia coder: hasta 2 ramas paralelas (`max_parallel_branches`)

---

## 8. CPU State Manager

Responsabilidades (no delegar al contexto LLM):

```text
state/sessions/<id>/
  session.db              # SQLite: mensajes, intent_spec, plan, facts
  task_graph.json         # nodos, depends_on, parallel_group
  mvf.json                # criterios + validated flag
  pipeline_runs/          # trazas estilo Hestia SubagentRunTrace
  experiences/            # sumarios post-misión
  operator_inbox.jsonl    # sugerencias no bloqueantes al humano
  runs/<phase>/manifest.json
  artifacts/
  llm_audit.jsonl
```

---

## 9. Bucle autónomo (misión nocturna)

Por cada nodo del TaskGraph:

```text
split (sintaxis acotada)
  → probe (run_script / snippet, artifact)
  → test (pytest / assert, CPU)
  → si fail: refine (VT, JSON delta) → patch (coder, diff mínimo)
  → si pass: consolidate (productor verificado)
  → summarize + embed experiencia (CPU)
  → siguiente nodo
```

**Productor consolidado** = pasó test. Repetición y autocorrección continua hasta MVF o STOP explícito.

Criterios de salida del bucle nocturno:

1. `mvf.validated === true` → fase `promote` (humano)
2. Negación explícita del operador
3. `max_steps` / presupuesto tiempo (configurable, con manifest `failed`)

---

## 10. Patrones Hestia adoptados

Referencia: `C:\Users\soyko\Downloads\Hestia-main\Hestia-main`

| Patrón Hestia | Adopción en pwsh_agent |
|---------------|------------------------|
| Skills Markdown+YAML | `knowledge/skills/` por dominio (PCAP, web_auth, code_build) |
| Subagents lineales | `knowledge/pipelines/*.yaml` para vertical slices |
| Diagnose → rewrite → validate | Optioneer → draft → test → refine |
| `/compact` memoria operativa | Extender trim: resumir turnos viejos, K literales |
| RAG como evidencia | Chunks tag `role: evidence` en inyección |
| Manifest por ejecución | `runs/<phase>/manifest.json` (schema kernel) |
| Promotion gate skills | `examples/` solo tras sanitizar; runtime privado |
| Fail-fast vs retry | Pipelines declarados: fail-fast; ReAct exploratorio: retry cap |

**No copiar:** checkpoints bloqueantes de Hestia (pwsh_agent ya tiene CheckpointGate — reorientar a MVF-first); embeddings hash-only.

---

## 11. Ecosistema cooperativo

| Sistema | Rol | Feed |
|---------|-----|------|
| repo-hygiene | Auditoría arquitectura | `hygiene-feed` |
| Editorial | Prosa larga | `editorial-feed` |
| exploration-kernel | Protocolos, schemas | `COOPERATION_AGENT.md` |

Lookup por ID en feeds — nunca inyectar manifest completo.

Jerarquía en conflicto (kernel): `manual/` > `canon.md` > `backlog.md` > informe LLM > extract JSON.

---

## 12. Handoffs JSON (contratos)

### IntentSpec (Intake → Optioneer)

Campos clave: `goal`, `domain`, `objectives[]`, `constraints[]`, `unknowns[]`, `deliverables[]`, `success_criteria[]`, `safety`, `mvf`.

### TaskGraph (Optioneer → Execute)

```json
{
  "chosen_option": "A",
  "tasks": [
    {
      "id": "1",
      "action": "string",
      "tool_hint": "write_file",
      "assigned_agent": "workspace",
      "depends_on": [],
      "success_criteria": "string",
      "tests": ["string"]
    }
  ]
}
```

### RefineDelta (test fail → patch)

```json
{
  "failed_check": "test_render",
  "diagnosis": "string",
  "patch_hint": "string",
  "retry_strategy": "patch|reoptioneer|skip"
}
```

---

## 13. Compactación y economía de tokens

**Leemos caro:** Intake 7B + index + Optioneer reciben comprensión amplia (digests, spec).

**Escribimos barato:** Coder recibe subtarea + último error + diff scope; `num_predict` acotado en probes.

**Promover humano:** Solo en `promote` y operaciones destructivas.

Reglas de inyección EXECUTE:

- Máximo: `IntentSpec` compact (~500 tok) + subtarea actual + último tool result
- No re-inyectar monólogo VT completo en roadmap (anti-redundancia)
- Historial: ventana K turnos + resumen operativo (Hestia `/compact`)

---

## 14. Vertical slice de referencia: HelloGame

Misión mínima para validar el canon completo:

1. **ingest** → IntentSpec `code_build`, deliverables `HelloGame/game.py`, `PLAN.md`
2. **index** → workspace vacío o existente
3. **optioneer** → A: stdin loop, B: ANSI grid → elige A
4. **draft** → scaffold + implement
5. **validate** → pytest smoke pasa
6. **mvf.validated** → true
7. **promote** → operador revisa; silencio no revierte MVF

Smoke parcial existente: `tools_dev/vertical_smoke.py` (INTAKE→PLAN→VALIDATE roadmap).  
E2E objetivo: `tools_dev/night_mission.py` + MVF checks en disco.

---

## 15. Roadmap de implementación

| Orden | Entregable | Desbloquea |
|-------|------------|------------|
| R1 | `docs/AGENT_CANON.md` (este doc) | Alineación |
| R1b | `docs/ORCHESTRATOR_CANON.md` + Pulse Queue | Cola unificada, idle/noche |
| R1c | Swap 1070 + modelos residentes | EXECUTE en GPU no idle — [plans/gpu_allocation_plan.md](plans/gpu_allocation_plan.md) |
| T1 | Parser + tool loop EXECUTE | Throughput code_build — [plans/tool_loop_plan.md](plans/tool_loop_plan.md) |
| R2 | Perfil `mvf_autonomous` en checkpoint | Misiones nocturnas sin bloqueo |
| R3 | `core/optioneer.py` + OptionsPack | Estrategia antes de código |
| R4 | `mvf.json` + validador CPU | Goal lock verificable |
| R5 | HelloGame E2E con tests | Demo funcional |
| R6 | `knowledge/pipelines/code_build.yaml` | Subagent Hestia-style |
| R7 | Bucle probe/test/refine | Autocorrección |
| R8 | `experience_store` + embed CPU | Sorprender mañana con lo aprendido anoche |
| R9 | Orquestador paralelo post-Optioneer | 3 GPUs concurrentes |

**Planes de implementación detallados:** [plans/MVF_AGENCY_ROADMAP.md](plans/MVF_AGENCY_ROADMAP.md) (orden, dependencias, runbook operativo).

---

## 16. Referencias

| Documento | Path |
|-----------|------|
| Pipeline vertical kernel | `exploration-kernel/protocols/vertical_pipeline.md` |
| Cooperación multi-GPU | `exploration-kernel/protocols/COOPERATION_AGENT.md` |
| Model routing | `exploration-kernel/protocols/model_routing.yaml` |
| Knowledge base VRAM/KV | `docs/knowledge.md` |
| Agent loop actual | `docs/agent-loop.md` |
| Checkpoints | `core/user_checkpoint.py`, `config.yaml` → `checkpoint` |
| Intent pipeline | `core/intent_spec.py`, `core/model_dispatch.py` |
| Ollama deploy | `Ollama/docker/DEPLOYMENT.md`, `MODELS.md` |
| Hestia architecture | `Hestia-main/docs/02-architecture.md`, `04-skills-and-subagents.md` |
| Run manifest schema | `exploration-kernel/schemas/run_manifest.schema.json` |
| **Orchestrator canon** | `docs/ORCHESTRATOR_CANON.md` |
| Pulse Queue CLI | `pulse_queue.py` |

---

*Canon vivo: actualizar cuando cambien caps de contexto, perfiles checkpoint, o fases implementadas.*
