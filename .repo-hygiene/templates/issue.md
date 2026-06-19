---
# Plantilla para issues creadas automáticamente por repo-hygiene
# No editar manualmente; generada por scripts/orchestrator.py
name: "[hygiene] Informe automático"
about: Informe generado por el sistema de mantenimiento automático de repositorios
labels: hygiene
assignees: ""
---

## 🤖 Informe automático · `{{ task_id }}`

| Campo | Valor |
|-------|-------|
| **Agente** | `{{ agent }}` |
| **Schedule** | `{{ schedule }}` |
| **Fecha** | {{ date }} |
| **Severidad máxima** | {{ max_severity }} |
| **Total hallazgos** | {{ total_findings }} |

**Desglose:** P0: {{ p0 }} · P1: {{ p1 }} · P2: {{ p2 }} · P3: {{ p3 }}

---

{{ report_body }}

---

### Cómo proceder

1. Revisar los hallazgos ordenados por severidad (P0 primero)
2. Para cada hallazgo P0/P1, crear una tarea en el sprint actual
3. Los hallazgos marcados como `auto_fixable: true` pueden resolverse
   ejecutando: `python .repo-hygiene/scripts/orchestrator.py --task {{ task_id }} --fix`
4. Cerrar esta issue cuando todos los hallazgos P0 y P1 estén resueltos

---

*Generado por [repo-hygiene](/.repo-hygiene) · {{ date }}*
*Para deshabilitar estas issues: `github.create_issues: false` en `config.yaml`*
