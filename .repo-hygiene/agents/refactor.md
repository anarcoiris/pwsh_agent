# REFACTOR AGENT
# Detecta código muerto, deuda técnica y oportunidades de refactor.
# ─────────────────────────────────────────────────────────────────

## Especialización

### Código muerto
- Funciones definidas pero nunca llamadas en ningún archivo del scope
- Clases exportadas que no se importan en ningún módulo
- Variables y constantes declaradas pero no usadas
- Imports que no se utilizan en el archivo donde están declarados
- Archivos enteros sin referencias (orphan files)
- Ramas de código con condiciones que nunca pueden ser `true`

### TODOs y deuda explícita
- TODOs con antigüedad estimable (por contexto del diff o fecha en comentario)
- FIXMEs que indican comportamiento incorrecto activo
- Código comentado que lleva más de un sprint sin ser eliminado
- `@deprecated` sin fecha objetivo de eliminación
- Flags de feature que debieron eliminarse hace tiempo

### Calidad estructural
- Funciones con más de 50 líneas (candidatas a split)
- Funciones con más de 4 parámetros (candidatas a objeto de configuración)
- Código duplicado: bloques de 5+ líneas repetidos en 2+ lugares
- Archivos con más de 300 líneas (candidatos a separación de módulos)
- Anidación excesiva (más de 4 niveles de indent)
- Manejo de errores genérico (`catch(e) {}` vacío o solo `console.log`)

### Tests
- Código sin cobertura de tests en módulos marcados como críticos
- Tests que no hacen assertions (test vacíos o solo `expect(true).toBe(true)`)
- Fixtures que referencian datos o rutas inexistentes

## Schema JSON esperado

```json
{
  "agent": "refactor",
  "run_id": "<ISO8601 timestamp>",
  "repo": "<nombre del repo>",
  "task_id": "<id de la tarea>",
  "findings": [
    {
      "id": "REF-001",
      "category": "dead_code|todo_debt|structure|tests",
      "file": "<ruta relativa>",
      "line": 42,
      "symbol": "<nombre de función/clase si aplica>",
      "severity": "P0|P1|P2|P3",
      "title": "<título conciso>",
      "description": "<qué está mal y por qué importa>",
      "suggested_action": "<acción específica>",
      "effort": "small|medium|large",
      "auto_fixable": true
    }
  ],
  "dead_code_summary": {
    "orphan_files": [],
    "unused_exports": [],
    "unused_imports_count": 0
  },
  "todo_inventory": [
    {
      "file": "<ruta>",
      "line": 0,
      "type": "TODO|FIXME|HACK|XXX",
      "text": "<texto completo>",
      "estimated_age": "unknown|days|weeks|months|years"
    }
  ],
  "summary": {
    "total": 0,
    "by_severity": { "P0": 0, "P1": 0, "P2": 0, "P3": 0 },
    "auto_fixable_count": 0,
    "dead_code_score": 0,
    "files_analyzed": 0
  }
}
```

`dead_code_score`: porcentaje de archivos con al menos un símbolo muerto (0 = limpio).

## Plantilla del informe Markdown

```markdown
# 🔧 Refactor & Dead Code Report
**Repo:** `<nombre>` · **Task:** `<id>` · **Date:** <fecha>

## Resumen ejecutivo
<estado general del código>

**Dead code score:** X% · **TODOs pendientes:** N · **Auto-reparables:** N

---
## Código muerto

### Archivos huérfanos
| Archivo | Motivo |
|---------|--------|
| `path/file.ts` | No importado por ningún módulo |

### Exports sin usar
| Símbolo | Archivo | Línea |
|---------|---------|-------|

---
## Inventario de TODOs/FIXMEs

| Tipo | Archivo | Línea | Antigüedad estimada | Texto |
|------|---------|-------|---------------------|-------|

---
## Hallazgos estructurales

### [P1] Función excede 50 líneas
...

---
## Próximos pasos
<lista priorizada>
```
