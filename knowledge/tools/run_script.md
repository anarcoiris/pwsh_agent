---
tools: [run_script, host_exec]
phase: [development]
---

# run_script Tool Playbook

## Run Python Deliverables

**Use tool `run_script`** — not `host_exec` — for all `.py` files.

```json
{"name": "run_script", "arguments": {"script_path": "watcher/watcher.py"}}
```

The agent uses the project `.venv/Scripts/python.exe` automatically. Never use `powershell -File script.py`.

## Install Missing Python Modules

If `run_script` returns `missing_module` in stderr, install into the venv:

```json
{"name": "host_exec", "arguments": {"command": "& \".venv/Scripts/python.exe\" -m pip install watchdog"}}
```

Or use the venv-aware pip form the ExecutionPolicy normalizes automatically.

## Common Errors

| Error | Fix |
|-------|-----|
| `ModuleNotFoundError: No module named 'X'` | `pip install X` via venv python |
| `powershell -File *.py` rejected | Use `run_script` instead |
| Script not found | Verify path with `read_file` first |
