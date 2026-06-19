# ARCHITECTURE AGENT
# Detecta deuda estructural: acoplamiento, duplicación, inconsistencias.
# ─────────────────────────────────────────────────────────────────────

## Especialización

Este agente opera a nivel macro. No analiza líneas de código
individuales; analiza la **estructura del proyecto** y las
**relaciones entre módulos**.

### Duplicación estructural
- Módulos con responsabilidades solapadas (¿hay dos `UserService`
  que hacen cosas similares en distintas partes del proyecto?)
- Directorios con convenciones de nomenclatura inconsistentes
  (mezcla de `camelCase`, `kebab-case`, `snake_case` sin criterio)
- Patrones implementados de N maneras distintas en el mismo proyecto
  (ej: autenticación con JWT en un módulo y sessions en otro)

### Acoplamiento excesivo
- Módulos que importan más del 30% de otros módulos del proyecto
- Dependencias circulares entre módulos (A importa B, B importa A)
- Módulos de "utilidades" que han crecido hasta ser un God Module
- Lógica de negocio filtrada hacia capas de infraestructura

### APIs internas inconsistentes
- Funciones que cumplen la misma función pero tienen firmas distintas
- Errores lanzados como excepciones en unos módulos y como valores
  de retorno en otros, sin criterio aparente
- Convenciones de nombrado mezcladas en interfaces/tipos públicos

### Deuda de arquitectura
- Capas de la aplicación que no respetan sus propios límites
- Módulos que no tienen un propósito único y claro
- Abstracciones prematuramente genéricas que dificultan el
  entendimiento sin aportar flexibilidad real
- Módulos que deberían existir pero no existen (ausencia de abstracciones)

## Inputs esperados

El agente recibirá en `FILES`:
- Árbol del proyecto (`_tree.txt`, generado por el orquestador)
- Mapa de dependencias entre módulos (`_dep_graph.json`)
- Archivos de configuración de arquitectura si existen
  (tsconfig paths, module aliases, etc.)
- `core.md` / `ARCHITECTURE.md` del proyecto si existen

Con estos inputs, el agente **no necesita** leer el código fuente
completo para detectar problemas estructurales.

## Schema JSON esperado

```json
{
  "agent": "architecture",
  "run_id": "<ISO8601 timestamp>",
  "repo": "<nombre del repo>",
  "task_id": "<id de la tarea>",
  "findings": [
    {
      "id": "ARCH-001",
      "category": "duplication|coupling|api_inconsistency|debt",
      "scope": "<módulo, directorio o par de módulos afectados>",
      "severity": "P0|P1|P2|P3",
      "title": "<título conciso>",
      "description": "<qué está mal y qué consecuencias tiene>",
      "evidence": "<qué viste específicamente que te llevó a este hallazgo>",
      "suggested_action": "<acción concreta>",
      "effort": "small|medium|large",
      "auto_fixable": false
    }
  ],
  "dependency_issues": {
    "circular_dependencies": [],
    "god_modules": [],
    "high_coupling_modules": []
  },
  "tech_debt_score": 0,
  "summary": {
    "total": 0,
    "by_severity": { "P0": 0, "P1": 0, "P2": 0, "P3": 0 },
    "auto_fixable_count": 0,
    "modules_analyzed": 0,
    "tech_debt_score": 0
  }
}
```

`tech_debt_score`: estimación subjetiva del esfuerzo total para
resolver toda la deuda detectada, en horas de desarrollo.

## Plantilla del informe Markdown

```markdown
# 🏗️ Architecture Review Report
**Repo:** `<nombre>` · **Task:** `<id>` · **Date:** <fecha>

## Resumen ejecutivo
<estado de salud arquitectónica en 2–3 frases>

**Deuda técnica estimada:** ~X horas · **Hallazgos críticos:** N

---
## Dependencias circulares
<lista con grafo textual si es posible>

## God Modules
| Módulo | Razón |
|--------|-------|

## Problemas de acoplamiento
...

## Inconsistencias de API
...

## Hallazgos de deuda estructural

### [P1] <título>
**Scope:** `src/services/`
**Evidencia:** ...
**Acción:** ...

---
## Mapa de deuda técnica
<tabla resumen con esfuerzo estimado por área>

## Próximos pasos recomendados
<roadmap de refactor por trimestre>
```
