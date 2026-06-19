# Sesión de diseño 2026-06-19 — archivo de referencia

Resumen consolidado de la conversación: multi-GPU, KV Q4, probe VRAM, pipeline vertical, Optioneer, MVF-first, Hestia, orquestador unificado.

## Documentos canónicos derivados

| Documento | Contenido |
|-----------|-----------|
| [../AGENT_CANON.md](../AGENT_CANON.md) | Pipeline 10 fases, Optioneer, goal lock, MVF, multi-GPU, Hestia |
| [../ORCHESTRATOR_CANON.md](../ORCHESTRATOR_CANON.md) | Cola unificada, schedules, idle/night, CLI Pulse Queue |
| [../plans/MVF_AGENCY_ROADMAP.md](../plans/MVF_AGENCY_ROADMAP.md) | **Roadmap implementación** R2–R7 + cola + R1c/T1 — orden, contratos, runbook |
| [../plans/gpu_allocation_plan.md](../plans/gpu_allocation_plan.md) | Swap 1070, modelos residentes |
| [../plans/tool_loop_plan.md](../plans/tool_loop_plan.md) | Parser, sequentialthinking, tool_agent |
| [../plans/README.md](../plans/README.md) | Índice de planes |
| [../../memory/2026-06-19.md](../../memory/2026-06-19.md) | Log operativo del día |

## Hallazgos clave

### Hardware y contexto (probe idle 2026-06-19)

| Rol | GPU | num_ctx |
|-----|-----|---------|
| Intake | 1070 #2 | 8192 |
| Planner | 1080 | 32768 |
| Coder | 1070 #1 | 16384 |

Contención con `ai_reviewer.py` (3 workers) invalidó el primer probe coder 16k; probe idle confirmó viabilidad.

### Pipeline vertical vs implementado

```
ingest → index → analyze → triage → OPTIONEER → draft ⇄ test/refine → validate → promote
```

Hoy: INTAKE → PLAN → VALIDATE → EXECUTE → EVALUATE (Optioneer colapsado en PLAN).

### Regla Goal Lock / MVF-first

Tras fijar goal: silencio y sugerencias de aclaración **no bloquean**. Solo negación explícita o MVF + gate promote humano.

### Principio local

**Leemos caro, escribimos barato. Promover humano.**

### Hestia (referencia)

`C:\Users\soyko\Downloads\Hestia-main\Hestia-main` — skills/subagents declarativos, trazas, compactación, RAG como evidencia, promotion gate.

### Smoke vertical HelloGame

`tools_dev/vertical_smoke.py` — INTAKE → PLAN → VALIDATE roadmap OK; EXECUTE E2E pendiente.

## Roadmap implementación agente

R1 canon ✓ | R1b Pulse Queue ✓ | **R1c GPU swap** | **T1 tool loop** | R2 mvf_autonomous | R3 optioneer | R4 mvf.json | R5 HelloGame E2E | R6 pipelines YAML | R7 probe/test loop | R8 experience store | R9 paralelismo GPU

## Orquestador (esta sesión)

Unificar en Pulse Queue: misiones pwsh_agent, hygiene review, editorial, schedules por idle/noche/cron/prioridad.
