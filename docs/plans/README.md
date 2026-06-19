# Plans index

Session architecture overview: [../agent-loop.md](../agent-loop.md).

**Canon normativo:** [../AGENT_CANON.md](../AGENT_CANON.md) · **Orquestador:** [../ORCHESTRATOR_CANON.md](../ORCHESTRATOR_CANON.md) · **Diseño 2026-06-19:** [../reference/DESIGN_SESSION_2026-06-19.md](../reference/DESIGN_SESSION_2026-06-19.md)

---

## MVF + Agencia roadmap (activo)

**Coordinación maestra:** [MVF_AGENCY_ROADMAP.md](./MVF_AGENCY_ROADMAP.md) — orden, dependencias, contratos compartidos, runbook operativo.

```text
0  agency_audit + loop_restrictions (baseline + guards)
0a gpu_allocation (R1c) — swap 1070, recom. antes R5
0c tool_loop (T1) — parser + ST, paralelo R4
1  mvf_autonomous (R2)
2  mvf_validator (R4)
3  loop_restrictions (implementar relajaciones + T1-A/D)
4  probe_test_refine (R7)
5  pulse_queue_agency (Q4)
6  hello_game_e2e (R5)
—  OptionsPack/ (R3 diferido)
```

| Plan | Status | ID | Summary |
|------|--------|-----|---------|
| [MVF_AGENCY_ROADMAP.md](./MVF_AGENCY_ROADMAP.md) | **ACTIVE** | — | Índice maestro, compatibilidad, runbook |
| [agency_audit_plan.md](./agency_audit_plan.md) | PROPOSED | 0 | Harness R+P+A+V+I, baseline |
| [loop_restrictions_review.md](./loop_restrictions_review.md) | PROPOSED | 0b | Guards conflictivos + relajación MVF-first |
| [gpu_allocation_plan.md](./gpu_allocation_plan.md) | PROPOSED | R1c | Swap 1070, INTAKE/EXECUTE ports, tool_agent slot |
| [tool_loop_plan.md](./tool_loop_plan.md) | PROPOSED | T1 | Parser 1 acción/turno, ST aislado, concurrencia |
| [mvf_autonomous_plan.md](./mvf_autonomous_plan.md) | PROPOSED | R2 | Checkpoints notify-only, operator_inbox |
| [mvf_validator_plan.md](./mvf_validator_plan.md) | PROPOSED | R4 | `mvf.json`, CPU validator, gate complete |
| [probe_test_refine_plan.md](./probe_test_refine_plan.md) | PROPOSED | R7 | Bucle probe/test/refine |
| [pulse_queue_agency_plan.md](./pulse_queue_agency_plan.md) | PROPOSED | Q4 | Cola mutável, hygiene migration |
| [hello_game_e2e_plan.md](./hello_game_e2e_plan.md) | PROPOSED | R5 | `night_mission.py`, demo HelloGame |
| [OptionsPack/](./OptionsPack/) | DEFERRED | R3 | Optioneer borrador |

---

## Planes históricos

| Plan | Status | Summary |
|------|--------|---------|
| [session_closure_20260604.md](./session_closure_20260604.md) | **CLOSED** | Specialist handoff fixes verified |
| [specialist_handoff_plan.md](./specialist_handoff_plan.md) | **DONE** | Prompt pack + delegate_to architecture |
| [robustness_stack_plan.md](./robustness_stack_plan.md) | **DONE** | Circuit Breaker, Scheduler, Session DB |
| [web_auth_html_pipeline_plan.md](./web_auth_html_pipeline_plan.md) | **PHASE 1–2 DONE** | HTML/XML router login pipeline |
| [context_retry_stack_20260610.md](./context_retry_stack_20260610.md) | **DONE** | LLM audit, context diet, retry stack |
| [implementation_plan.md](./implementation_plan.md) | **DONE** | Batch notes + artifact compaction |
| [context_trim_plan.md](./context_trim_plan.md) | **REFERENCE** | Codebase map + context audit |
| [Generalization/multi_purpose_agent_design.md](./Generalization/multi_purpose_agent_design.md) | **PART DONE** | Intent Spec, planner, advisory gates |
| [Generalization/consolidated_generalization_plan.md](./Generalization/consolidated_generalization_plan.md) | **SUPERSEDED** | Superseded by multi_purpose_agent_design |
| [micro-core/](./micro-core/) | **PROPOSAL** | Script-builder micro agent |
