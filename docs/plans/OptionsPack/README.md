# OptionsPack — Optioneer (R3)

> **Status:** DEFERRED  
> **Roadmap:** R3  
> **Prioridad:** Post-R5 (después de HelloGame MVF)  
> **Roadmap maestro:** [../MVF_AGENCY_ROADMAP.md](../MVF_AGENCY_ROADMAP.md)

Optioneer elige **estrategia** antes de generación cara — no es planificar pasos. Este directorio guarda el borrador normativo para implementación futura.

---

## Documentos

| Archivo | Contenido |
|---------|-----------|
| [optioneer_draft.md](./optioneer_draft.md) | Contrato OptionsPack, flujo, módulo `core/optioneer.py` |

---

## Por qué está diferido

1. **HelloGame MVF** no requiere OptionsPack — PLAN actual (`IntentPlanner`) basta para scaffold + pytest.
2. Prioridad acordada: **R2 + R4 + R5 + R7 + cola** antes de R3.
3. Optioneer depende parcialmente de **index/RAG** (fase `index` aún parcial).

---

## Dependencias para activar R3

| Prerequisito | Estado |
|--------------|--------|
| R4 MVF validator | [mvf_validator_plan.md](../mvf_validator_plan.md) — PROPOSED |
| R5 HelloGame E2E | [hello_game_e2e_plan.md](../hello_game_e2e_plan.md) — PROPOSED |
| Index digest pre-plan | Parcial (hygiene feed, RAG) |
| Micro-probes coder | ReAct EXECUTE existente |

---

## Canon

Normativo completo: [AGENT_CANON.md](../../AGENT_CANON.md) §5 (Optioneer).

---

## Orden sugerido cuando se retome

```text
1. index digest explícito pre-optioneer
2. core/optioneer.py + OptionsPack schema
3. Insertar fase entre INTAKE y PLAN en agent.py
4. Micro-probes paralelos (≤10 líneas)
5. CPU elige recommendation → TaskGraph
```

---

*No implementar código hasta cerrar R5 y validar baseline en [agency_audit_plan.md](../agency_audit_plan.md).*
