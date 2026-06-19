# code_build — entrega verificable

Misiones que crean archivos/directorios (generación masiva, scaffolding, demos).

## Reglas

1. **Un artefacto → `write_file`** (o `run_script` con generador `.py` que escriba en disco).
2. **No usar `host_exec` con bucles PowerShell** para crear N archivos — bloqueado en runtime.
3. **`append_note`** solo tras verificar que el path existe y el conteo coincide.
4. **MVF** puede exigir `dir_exists` + `dir_count` — la misión no cierra sin evidencia CPU.
5. **Sin playbooks hygiene/REF-001** salvo que el prompt lo pida explícitamente.

## Patrón recomendado (100 archivos)

```text
1. write_file scripts/generate_relatos.py  (generador determinista)
2. run_script scripts/generate_relatos.py
3. append_note solo si ejemplos-texto/ tiene ≥ N *.md
4. MISSION_COMPLETE cuando MVF pase
```

## Anti-patrones (incidente ejemplos-texto 2026-06-19)

- `host_exec` con `for ($i=1; $i -le 100; ...)` — `$i` se pierde entre invocaciones.
- `append_note` afirmando "100 relatos" sin directorio en disco.
- `delegate_to` desde workspace en misión LEAD-only.
- Cerrar por `max_steps` con MVF pendiente.
