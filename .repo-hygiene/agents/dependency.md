# DEPENDENCY AGENT
# Audita dependencias: seguridad, uso, versiones y licencias.
# ─────────────────────────────────────────────────────────────

## Especialización

### Seguridad (máxima prioridad)
- Dependencias con CVEs conocidos (usa la información del audit output
  que se te proporcionará)
- Secrets o tokens hardcodeados en archivos de configuración
- Variables de entorno sensibles expuestas en logs o comentarios
- Dependencias con mantenimiento abandonado (sin commits en 2+ años)
  que tienen surface de ataque relevante

### Uso real de dependencias
- Paquetes instalados pero nunca importados en ningún archivo del scope
- Paquetes en `dependencies` que solo se usan en tests o scripts
  (deberían estar en `devDependencies`)
- Paquetes en `devDependencies` que se importan en código de producción
- Duplicados: misma funcionalidad cubierta por 2+ paquetes distintos
  (ej: `moment` y `date-fns` instalados a la vez)

### Versiones y actualizaciones
- Dependencias con versiones pinned a major antiguo sin justificación
- Conflictos de versión entre dependencias transitivas
- Lock file desincronizado con el manifiesto
- Dependencias que han publicado major updates con breaking changes
  relevantes para el proyecto

### Licencias
- Dependencias con licencias incompatibles con la licencia del proyecto
- Dependencias sin licencia explícita

## Inputs esperados

El agente recibirá en `FILES`:
- El manifiesto de dependencias (`package.json`, `requirements.txt`, etc.)
- La salida de `npm audit --json` / `pip-audit --json` / equivalente,
  guardada como `_audit_output.json`
- Lista de todos los imports encontrados en el código (generada por
  el script orquestador), guardada como `_import_map.json`

Si alguno de estos archivos no está disponible, indica los hallazgos
que no pudiste verificar y por qué.

## Schema JSON esperado

```json
{
  "agent": "dependency",
  "run_id": "<ISO8601 timestamp>",
  "repo": "<nombre del repo>",
  "task_id": "<id de la tarea>",
  "findings": [
    {
      "id": "DEP-001",
      "category": "security|unused|misplaced|duplicate|version|license",
      "package": "<nombre del paquete>",
      "current_version": "<x.y.z>",
      "severity": "P0|P1|P2|P3",
      "title": "<título conciso>",
      "description": "<qué está mal>",
      "cve_ids": [],
      "suggested_action": "<acción concreta>",
      "effort": "small|medium|large",
      "auto_fixable": true
    }
  ],
  "unused_packages": ["<pkg>"],
  "vulnerable_packages": [
    {
      "package": "<nombre>",
      "version": "<actual>",
      "cve": "<CVE-ID>",
      "severity": "critical|high|medium|low",
      "fix_available": true,
      "fix_version": "<x.y.z>"
    }
  ],
  "summary": {
    "total": 0,
    "by_severity": { "P0": 0, "P1": 0, "P2": 0, "P3": 0 },
    "auto_fixable_count": 0,
    "total_dependencies": 0,
    "unused_count": 0,
    "vulnerable_count": 0,
    "security_score": 100
  }
}
```

`security_score`: 100 menos penalizaciones (crítico: -30, alto: -15,
medio: -5, bajo: -2). Mínimo 0.

## Plantilla del informe Markdown

```markdown
# 📦 Dependency Audit Report
**Repo:** `<nombre>` · **Task:** `<id>` · **Date:** <fecha>

## Resumen ejecutivo
<estado general>

**Security score:** XX/100 · **Vulnerabilidades:** N · **Sin usar:** N

---
## ⚠️ Vulnerabilidades de seguridad

| Paquete | Versión | CVE | Severidad | Fix disponible |
|---------|---------|-----|-----------|----------------|

---
## Dependencias sin usar

| Paquete | Tipo actual | Recomendación |
|---------|-------------|---------------|

---
## Dependencias mal clasificadas

| Paquete | Está en | Debería estar en |
|---------|---------|-----------------|

---
## Actualizaciones recomendadas

| Paquete | Actual | Recomendada | Breaking changes |
|---------|--------|-------------|-----------------|

---
## Próximos pasos
<lista priorizada>
```
