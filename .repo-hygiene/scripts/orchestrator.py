#!/usr/bin/env python3
"""
repo-hygiene · Orchestrator
Carga config.yaml, selecciona tareas por schedule, invoca agentes
con contexto aislado y publica resultados.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

# ─────────────────────────────────────────────────────────────
#  Constantes
# ─────────────────────────────────────────────────────────────
HYGIENE_DIR = Path(__file__).parent.parent        # .repo-hygiene/
AGENTS_DIR  = HYGIENE_DIR / "agents"
CONFIG_FILE = HYGIENE_DIR / "config.yaml"
REPO_ROOT   = Path.cwd()
REPORTS_DIR = REPO_ROOT / ".reports" / "hygiene"

AUDIT_CMDS = {
    "package.json":       ["npm", "audit", "--json"],
    "requirements.txt":   ["pip-audit", "--format", "json"],
    "pyproject.toml":     ["pip-audit", "--format", "json"],
    "Cargo.toml":         ["cargo", "audit", "--json"],
    "go.mod":             ["govulncheck", "-json", "./..."],
}


# ─────────────────────────────────────────────────────────────
#  Utilidades de configuración
# ─────────────────────────────────────────────────────────────
def load_config() -> dict:
    if not CONFIG_FILE.exists():
        print(f"[ERROR] No se encontró {CONFIG_FILE}", file=sys.stderr)
        sys.exit(1)
    with open(CONFIG_FILE) as f:
        return yaml.safe_load(f)


def tasks_for_schedule(config: dict, schedule: str) -> list:
    return [t for t in config.get("tasks", []) if t["schedule"] == schedule]


# ─────────────────────────────────────────────────────────────
#  Recolección de contexto
# ─────────────────────────────────────────────────────────────
def read_project_context(config: dict) -> str:
    """Carga los ficheros de contexto del proyecto (core.md, README, etc.)"""
    parts = []
    for pattern in config.get("project_context", []):
        for path in sorted(REPO_ROOT.glob(pattern)):
            if path.is_file():
                try:
                    content = path.read_text(encoding="utf-8", errors="replace")
                    parts.append(f"=== {path.relative_to(REPO_ROOT)} ===\n{content}")
                except OSError:
                    continue
    return "\n\n".join(parts) if parts else "(no se encontraron ficheros de contexto)"


def collect_files(task: dict, config: dict) -> dict[str, str]:
    """Recoge los archivos del scope de la tarea respetando límites de tamaño."""
    max_files   = config["budget"]["max_files_per_task"]
    max_size_kb = config["budget"]["max_file_size_kb"]
    files: dict[str, str] = {}

    includes = task["scope"].get("include", [])
    excludes = set(task["scope"].get("exclude", []))

    def is_excluded(path: Path) -> bool:
        rel = str(path.relative_to(REPO_ROOT))
        return any(path.match(ex) or rel.startswith(ex.rstrip("*").rstrip("/"))
                   for ex in excludes)

    for pattern in includes:
        for path in sorted(REPO_ROOT.glob(pattern)):
            if len(files) >= max_files:
                break
            if path.is_file() and not is_excluded(path):
                size_kb = path.stat().st_size / 1024
                try:
                    content = path.read_text(encoding="utf-8", errors="replace")
                    if size_kb > max_size_kb:
                        content = content[: max_size_kb * 1024] + "\n... [TRUNCADO]"
                    files[str(path.relative_to(REPO_ROOT))] = content
                except OSError:
                    continue

    return files


def generate_tree(max_depth: int = 4) -> str:
    """Genera árbol de directorios usando find (sin node_modules/.venv/etc.)"""
    excludes = ("node_modules", ".venv", "venv", "__pycache__",
                ".git", "dist", "build", ".next", "target")
    try:
        result = subprocess.run(
            ["find", ".", "-maxdepth", str(max_depth),
             "-not", "-path", "./.git/*"] +
            [item for ex in excludes for item in ["-not", "-path", f"./{ex}/*"]],
            capture_output=True, text=True, cwd=REPO_ROOT, timeout=15
        )
        lines = sorted(result.stdout.strip().splitlines())
        return "\n".join(lines[:1000])   # cap a 1000 líneas
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return "(no disponible)"


def run_audit(manifest_file: str) -> str | None:
    cmd = AUDIT_CMDS.get(manifest_file)
    if not cmd or not (REPO_ROOT / manifest_file).exists():
        return None
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, cwd=REPO_ROOT, timeout=60
        )
        return result.stdout if result.stdout.strip() else None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def build_import_map(files: dict[str, str]) -> dict:
    """Extrae mapa básico de imports del código fuente."""
    import_map: dict[str, list[str]] = {}
    patterns = {
        "js_ts":  re.compile(r'(?:import|require)\s*[(\'"]([\w@./-]+)[\'")]'),
        "python": re.compile(r'^\s*(?:from|import)\s+([\w.]+)', re.MULTILINE),
    }
    for filepath, content in files.items():
        imports = []
        for pat in patterns.values():
            imports.extend(pat.findall(content))
        if imports:
            import_map[filepath] = list(set(imports))
    return import_map


def read_existing_issues() -> list[dict]:
    """Lee issues abiertas de GitHub via CLI para evitar duplicados."""
    try:
        result = subprocess.run(
            ["gh", "issue", "list", "--label", "hygiene",
             "--state", "open", "--json", "number,title,labels"],
            capture_output=True, text=True, timeout=20
        )
        if result.returncode == 0:
            return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
        pass
    return []


# ─────────────────────────────────────────────────────────────
#  Carga de prompts de agentes
# ─────────────────────────────────────────────────────────────
def load_agent_prompt(agent_name: str) -> str:
    base = (AGENTS_DIR / "base.md").read_text(encoding="utf-8")
    specific_path = AGENTS_DIR / f"{agent_name}.md"
    specific = specific_path.read_text(encoding="utf-8") if specific_path.exists() else ""
    return f"{base}\n\n---\n\n{specific}"


# ─────────────────────────────────────────────────────────────
#  Construcción del mensaje de usuario para el agente
# ─────────────────────────────────────────────────────────────
def build_user_message(task: dict, files: dict[str, str],
                        project_context: str, extra: dict) -> str:
    files_section = "\n\n".join(
        f"<file path=\"{path}\">\n{content}\n</file>"
        for path, content in files.items()
    )

    extra_section = ""
    if extra.get("tree"):
        extra_section += f"\n<_tree>\n{extra['tree']}\n</_tree>"
    if extra.get("audit"):
        extra_section += f"\n<_audit_output>\n{extra['audit']}\n</_audit_output>"
    if extra.get("import_map"):
        extra_section += f"\n<_import_map>\n{json.dumps(extra['import_map'], indent=2)}\n</_import_map>"
    if extra.get("existing_issues"):
        extra_section += (f"\n<existing_issues>\n"
                          f"{json.dumps(extra['existing_issues'], indent=2)}\n"
                          f"</existing_issues>")

    run_id = datetime.now(timezone.utc).isoformat()
    repo_name = REPO_ROOT.name

    return f"""<task>
  <id>{task['id']}</id>
  <run_id>{run_id}</run_id>
  <repo>{repo_name}</repo>
  <title>{task['title']}</title>
  <objective>{task['description'].strip()}</objective>
</task>

<project_context>
{project_context}
</project_context>
{extra_section}

<files>
{files_section}
</files>

Analiza los archivos anteriores siguiendo tu especialización y las
instrucciones de formato de tu prompt de sistema. Produce el informe
completo con los dos bloques (JSON y Markdown) tal como se especifica."""


# ─────────────────────────────────────────────────────────────
#  Invocación del agente
# ─────────────────────────────────────────────────────────────
def invoke_agent(system_prompt: str, user_message: str,
                 config: dict) -> str:
    model_str = config["meta"].get("model", "")
    provider = config["meta"].get("model_provider", "")

    if "/" in model_str:
        p, m = model_str.split("/", 1)
        if p in ("anthropic", "gemini", "ollama"):
            provider = p
            model_name = m
        else:
            model_name = model_str
    else:
        model_name = model_str

    if not provider:
        if model_name.startswith("gemini"):
            provider = "gemini"
        elif model_name.startswith("claude"):
            provider = "anthropic"
        else:
            provider = "ollama"

    max_tokens = config["meta"].get("max_tokens_per_task", 8192)

    if provider == "anthropic":
        import anthropic
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            print("[ERROR] ANTHROPIC_API_KEY no está definida en las variables de entorno", file=sys.stderr)
            sys.exit(1)
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model_name,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        return response.content[0].text

    elif provider == "gemini":
        import requests
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            print("[ERROR] GEMINI_API_KEY no está definida en las variables de entorno", file=sys.stderr)
            sys.exit(1)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        headers = {"Content-Type": "application/json"}
        payload = {
            "systemInstruction": {
                "parts": [{"text": system_prompt}]
            },
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": user_message}]
                }
            ],
            "generationConfig": {
                "maxOutputTokens": max_tokens
            }
        }
        res = requests.post(url, headers=headers, json=payload)
        res.raise_for_status()
        data = res.json()
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as e:
            print(f"[ERROR] Estructura inesperada en respuesta de Gemini: {data}", file=sys.stderr)
            raise e

    elif provider == "ollama":
        import requests
        ollama_host = os.environ.get("OLLAMA_HOST", "http://localhost:11435")
        num_ctx = int(
            os.environ.get("OLLAMA_NUM_CTX")
            or config.get("ollama", {}).get("num_ctx", 8192)
        )
        url = f"{ollama_host.rstrip('/')}/v1/chat/completions"
        headers = {"Content-Type": "application/json"}
        payload = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ],
            "max_tokens": max_tokens,
            "temperature": 0.2,
            "options": {"num_ctx": num_ctx},
        }
        res = requests.post(url, headers=headers, json=payload)
        res.raise_for_status()
        data = res.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as e:
            print(f"[ERROR] Estructura inesperada en respuesta de Ollama: {data}", file=sys.stderr)
            raise e
    else:
        print(f"[ERROR] Proveedor de modelo '{provider}' no soportado.", file=sys.stderr)
        sys.exit(1)


# ─────────────────────────────────────────────────────────────
#  Parseo de la respuesta del agente
# ─────────────────────────────────────────────────────────────
def parse_response(raw: str) -> tuple[dict | None, str | None]:
    """Extrae JSON y Markdown de la respuesta del agente."""
    json_data = None
    md_report = None

    json_match = re.search(
        r"REPORT_JSON_START\s*(.*?)\s*REPORT_JSON_END",
        raw, re.DOTALL
    )
    if json_match:
        try:
            json_data = json.loads(json_match.group(1))
        except json.JSONDecodeError as e:
            print(f"  [WARN] No se pudo parsear JSON del agente: {e}", file=sys.stderr)

    md_match = re.search(
        r"REPORT_MARKDOWN_START\s*(.*?)\s*REPORT_MARKDOWN_END",
        raw, re.DOTALL
    )
    if md_match:
        md_report = md_match.group(1).strip()

    return json_data, md_report


# ─────────────────────────────────────────────────────────────
#  Guardado de informes
# ─────────────────────────────────────────────────────────────
def save_reports(task: dict, json_data: dict | None,
                 md_report: str | None, raw: str) -> Path:
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    report_dir = REPORTS_DIR / date_str / task["id"]
    report_dir.mkdir(parents=True, exist_ok=True)

    # Siempre guarda la respuesta cruda
    (report_dir / "raw.txt").write_text(raw, encoding="utf-8")

    if json_data:
        output_file = task.get("output", {}).get("data", f"{task['id']}.json")
        (report_dir / output_file).write_text(
            json.dumps(json_data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    report_path = None
    if md_report:
        output_file = task.get("output", {}).get("report", f"{task['id']}.md")
        report_path = report_dir / output_file
        report_path.write_text(md_report, encoding="utf-8")

    print(f"  ✓ Informes guardados en {report_dir.relative_to(REPO_ROOT)}")
    return report_dir


# ─────────────────────────────────────────────────────────────
#  Integración con GitHub
# ─────────────────────────────────────────────────────────────
def should_open_issue(task: dict, json_data: dict | None) -> bool:
    if not json_data:
        return False
    rule = task.get("actions", {}).get("open_issue_if", "never")
    if rule == "never":
        return False
    if rule == "always":
        return bool(json_data.get("findings"))
    summary = json_data.get("summary", {})
    by_sev  = summary.get("by_severity", {})
    if rule == "P0_only":
        return by_sev.get("P0", 0) > 0
    if rule == "P1_or_above":
        return by_sev.get("P0", 0) + by_sev.get("P1", 0) > 0
    if rule == "P2_or_above":
        return sum(by_sev.get(s, 0) for s in ["P0", "P1", "P2"]) > 0
    return False


def create_github_issue(task: dict, json_data: dict,
                         md_report: str, config: dict,
                         dry_run: bool) -> None:
    summary  = json_data.get("summary", {})
    by_sev   = summary.get("by_severity", {})
    total    = summary.get("total", 0)
    title    = f"[hygiene] {task['title']} — {total} hallazgos"

    sev_lines = " | ".join(
        f"{s}: {by_sev.get(s, 0)}" for s in ["P0", "P1", "P2", "P3"]
    )
    body = (
        f"## Resultado automatizado: `{task['id']}`\n\n"
        f"**Schedule:** `{task['schedule']}` · **Severidad:** {sev_lines}\n\n"
        f"---\n\n{md_report[:8000]}\n\n"
        f"---\n*Generado por repo-hygiene · {datetime.now().date()}*"
    )

    # Determina labels según severidad máxima
    max_sev = next(
        (s for s in ["P0", "P1", "P2", "P3"] if by_sev.get(s, 0) > 0), "P3"
    )
    labels = config["github"]["labels"].get(max_sev, ["hygiene"])

    if dry_run:
        print(f"  [DRY-RUN] Se abriría issue: {title}")
        print(f"           Labels: {labels}")
        return

    try:
        subprocess.run(
            ["gh", "issue", "create",
             "--title", title,
             "--body",  body,
             "--label", ",".join(labels)],
            check=True, capture_output=True, cwd=REPO_ROOT
        )
        print(f"  ✓ Issue creada: {title}")
    except subprocess.CalledProcessError as e:
        print(f"  [ERROR] No se pudo crear issue: {e.stderr.decode()}", file=sys.stderr)


def create_pr_for_fix(task: dict, finding: dict, filepath: str, dry_run: bool) -> None:
    finding_id = finding.get("id", "FIX")
    branch_name = f"hygiene/{task['id']}-{finding_id.lower()}"
    commit_message = f"[hygiene] Auto-fix {finding_id}: {finding.get('title')}"
    
    if dry_run:
        print(f"  [DRY-RUN] Se crearía rama '{branch_name}', commit '{commit_message}' y PR para {filepath}")
        return

    try:
        # 1. Comprobar si hay cambios
        status = subprocess.run(["git", "status", "--porcelain", filepath], capture_output=True, text=True, cwd=REPO_ROOT)
        if not status.stdout.strip():
            print(f"  [WARN] No hay cambios detectados en {filepath} para commitear.")
            return

        # 2. Crear y cambiar a rama
        subprocess.run(["git", "checkout", "-b", branch_name], check=True, capture_output=True, cwd=REPO_ROOT)
        
        # 3. Add y Commit
        subprocess.run(["git", "add", filepath], check=True, capture_output=True, cwd=REPO_ROOT)
        subprocess.run(["git", "commit", "-m", commit_message], check=True, capture_output=True, cwd=REPO_ROOT)
        
        # 4. Push
        print(f"  → Subiendo rama {branch_name} a origin...")
        subprocess.run(["git", "push", "--set-upstream", "origin", branch_name], check=True, capture_output=True, cwd=REPO_ROOT)
        
        # 5. Crear PR con gh CLI
        print(f"  → Creando Pull Request en GitHub...")
        pr_title = f"[hygiene] Auto-fix {finding_id} — {finding.get('title')}"
        pr_body = f"""## 🤖 Auto-Fix automático · `{finding_id}`

Este PR fue generado de forma automática por `repo-hygiene` para resolver el hallazgo:

- **Tarea**: `{task['id']}`
- **Archivo**: `{filepath}`
- **Descripción**: {finding.get('description')}
- **Acción sugerida**: {finding.get('suggested_action')}

Por favor, revisa los cambios antes de fusionar.
"""
        subprocess.run(
            ["gh", "pr", "create", "--title", pr_title, "--body", pr_body, "--head", branch_name],
            check=True, capture_output=True, cwd=REPO_ROOT
        )
        print(f"  ✓ PR creada exitosamente para rama {branch_name}.")
        
        # Volvemos a la rama por defecto
        default_branch = config["meta"].get("default_branch", "main")
        subprocess.run(["git", "checkout", default_branch], check=True, capture_output=True, cwd=REPO_ROOT)

    except subprocess.CalledProcessError as e:
        err_msg = e.stderr.decode() if e.stderr else str(e)
        print(f"  [ERROR] Falló la operación de Git/PR: {err_msg}", file=sys.stderr)
        # Intentamos volver a la rama por defecto por si acaso
        try:
            default_branch = config["meta"].get("default_branch", "main")
            subprocess.run(["git", "checkout", default_branch], capture_output=True, cwd=REPO_ROOT)
        except Exception:
            pass


def apply_auto_fix(task: dict, finding: dict, config: dict, dry_run: bool) -> None:
    filepath = finding.get("file")
    if not filepath:
        print(f"  [WARN] Hallazgo {finding.get('id')} no especifica archivo. Saltando fix.")
        return

    full_path = REPO_ROOT / filepath
    if not full_path.exists():
        print(f"  [WARN] El archivo {filepath} no existe en el disco. Saltando fix.")
        return

    print(f"  → Aplicando auto-fix para {finding.get('id')} ({finding.get('title')}) en {filepath}...")
    
    try:
        original_code = full_path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        print(f"  [ERROR] No se pudo leer {filepath}: {e}")
        return

    system_prompt = """Eres un agente experto en refactorización y corrección de código.
Tu tarea es modificar el archivo de código proporcionado para resolver el hallazgo de auditoría especificado.
Debes realizar ÚNICAMENTE los cambios necesarios para resolver el problema (por ejemplo, remover imports no usados, corregir la línea indicada, etc.).
Mantén intacto el resto de la lógica, comentarios e indentación del archivo.

Devuelve el contenido completo del archivo corregido encerrado exactamente entre las etiquetas <fixed_code> y </fixed_code>. No incluyas explicaciones, markdown ni texto fuera de esas etiquetas.
"""

    user_message = f"""<finding>
  <id>{finding.get('id')}</id>
  <title>{finding.get('title')}</title>
  <description>{finding.get('description')}</description>
  <suggested_action>{finding.get('suggested_action')}</suggested_action>
  <file>{filepath}</file>
  <line>{finding.get('line')}</line>
  <symbol>{finding.get('symbol')}</symbol>
</finding>

<original_file_content path="{filepath}">
{original_code}
</original_file_content>

Por favor, corrige el archivo original basándote en el hallazgo anterior y devuelve todo el contenido del archivo corregido entre <fixed_code> y </fixed_code>."""

    if dry_run:
        print(f"  [DRY-RUN] Se corregiría el archivo {filepath} para {finding.get('id')}")
        return

    try:
        raw_response = invoke_agent(system_prompt, user_message, config)
        match = re.search(r"<fixed_code>\s*(.*?)\s*</fixed_code>", raw_response, re.DOTALL)
        if not match:
            match = re.search(r"<fixed_code>(?:\s*)(.*?)(?:\s*)</fixed_code>", raw_response, re.DOTALL)
            
        if not match:
            print(f"  [ERROR] No se pudo parsear <fixed_code> en la respuesta del agente para {finding.get('id')}.")
            return

        fixed_code = match.group(1)
        full_path.write_text(fixed_code, encoding="utf-8")
        print(f"  ✓ Archivo {filepath} corregido exitosamente.")

        if config["github"].get("create_prs", False):
            create_pr_for_fix(task, finding, filepath, dry_run)

    except Exception as e:
        print(f"  [ERROR] Falló la auto-corrección para {finding.get('id')}: {e}")


# ─────────────────────────────────────────────────────────────
#  Runner de una tarea
# ─────────────────────────────────────────────────────────────
def run_task(task: dict, config: dict, dry_run: bool, run_fix: bool = False) -> None:
    print(f"\n{'='*60}")
    print(f"  TAREA: {task['id']}  [{task['schedule'].upper()}]")
    print(f"  Agente: {task['agent']}")
    print(f"{'='*60}")

    # 1. Recolectar contexto
    print("  → Cargando contexto del proyecto...")
    project_ctx = read_project_context(config)

    print("  → Recolectando archivos del scope...")
    files = collect_files(task, config)
    print(f"     {len(files)} archivos cargados")

    # 2. Extras según agente
    extra: dict = {}
    agent = task["agent"]

    if agent == "architecture":
        print("  → Generando árbol de proyecto...")
        extra["tree"] = generate_tree()
        extra["import_map"] = build_import_map(files)

    if agent == "dependency":
        for manifest in AUDIT_CMDS:
            audit_out = run_audit(manifest)
            if audit_out:
                extra["audit"] = audit_out
                break
        extra["import_map"] = build_import_map(files)

    if config["github"].get("create_issues"):
        extra["existing_issues"] = read_existing_issues()

    # 3. Invocar agente con contexto fresco
    print("  → Invocando agente...")
    system_prompt = load_agent_prompt(agent)
    user_message  = build_user_message(task, files, project_ctx, extra)

    raw_response = invoke_agent(system_prompt, user_message, config)

    # 4. Parsear respuesta
    json_data, md_report = parse_response(raw_response)

    if json_data:
        s = json_data.get("summary", {})
        print(f"  → Hallazgos: {s.get('total', '?')} total | "
              f"P0:{s.get('by_severity', {}).get('P0', 0)} "
              f"P1:{s.get('by_severity', {}).get('P1', 0)} "
              f"P2:{s.get('by_severity', {}).get('P2', 0)}")

    # 5. Guardar informes
    save_reports(task, json_data, md_report, raw_response)

    # 5b. Aplicar auto-fix si corresponde
    if run_fix and json_data and "findings" in json_data:
        findings = json_data["findings"]
        fixable_findings = [f for f in findings if f.get("auto_fixable")]
        if fixable_findings:
            print(f"  → Encontrados {len(fixable_findings)} hallazgos auto-reparables.")
            for finding in fixable_findings:
                apply_auto_fix(task, finding, config, dry_run)
        else:
            print("  → No se encontraron hallazgos auto-reparables.")

    # 6. Publicar en GitHub si corresponde
    if config["github"].get("create_issues") and should_open_issue(task, json_data):
        print("  → Abriendo issue en GitHub...")
        create_github_issue(task, json_data, md_report or "", config, dry_run)


# ─────────────────────────────────────────────────────────────
#  CLI
# ─────────────────────────────────────────────────────────────
def main() -> None:
    if sys.platform.startswith("win"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except AttributeError:
            pass

    parser = argparse.ArgumentParser(description="repo-hygiene orchestrator")
    parser.add_argument(
        "--schedule", choices=["daily", "weekly", "monthly", "all"],
        required=True, help="Qué conjunto de tareas ejecutar"
    )
    parser.add_argument(
        "--task", default=None,
        help="Ejecutar solo una tarea específica (por id)"
    )
    parser.add_argument(
        "--fix", action="store_true",
        help="Aplicar auto-correcciones para hallazgos auto-reparables"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="No crear issues ni PRs, solo generar informes"
    )
    parser.add_argument(
        "--export-feed", action="store_true",
        help="Exportar hallazgos al feed neutro para pwsh_agent tras completar"
    )
    args = parser.parse_args()

    config = load_config()

    # Resolver el proveedor para validar la clave de API correspondiente
    model_str = config["meta"].get("model", "")
    provider = config["meta"].get("model_provider", "")
    if "/" in model_str:
        p, _ = model_str.split("/", 1)
        if p in ("anthropic", "gemini", "ollama"):
            provider = p
    if not provider:
        if model_str.startswith("gemini"):
            provider = "gemini"
        elif model_str.startswith("claude"):
            provider = "anthropic"
        else:
            provider = "ollama"

    if provider == "anthropic" and "ANTHROPIC_API_KEY" not in os.environ:
        print("[ERROR] ANTHROPIC_API_KEY no está definida en las variables de entorno", file=sys.stderr)
        sys.exit(1)
    elif provider == "gemini" and "GEMINI_API_KEY" not in os.environ:
        print("[ERROR] GEMINI_API_KEY no está definida en las variables de entorno", file=sys.stderr)
        sys.exit(1)
    dry_run = args.dry_run or config["meta"].get("dry_run", False)

    if dry_run:
        print("[DRY-RUN] Modo de solo lectura activo\n")

    # Selección de tareas
    if args.task:
        tasks = [t for t in config["tasks"] if t["id"] == args.task]
        if not tasks:
            print(f"[ERROR] Tarea '{args.task}' no encontrada", file=sys.stderr)
            sys.exit(1)
    elif args.schedule == "all":
        tasks = config["tasks"]
    else:
        tasks = tasks_for_schedule(config, args.schedule)

    if not tasks:
        print(f"No hay tareas para schedule='{args.schedule}'")
        return

    print(f"[repo-hygiene] {len(tasks)} tarea(s) · schedule={args.schedule}")
    print(f"[repo-hygiene] Repo: {REPO_ROOT.name}")
    print(f"[repo-hygiene] Modelo: {config['meta']['model']}\n")

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    errors = []
    for task in tasks:
        try:
            run_task(task, config, dry_run, run_fix=args.fix)
        except Exception as exc:
            print(f"  [ERROR] Tarea {task['id']} fallida: {exc}", file=sys.stderr)
            errors.append((task["id"], str(exc)))

    print(f"\n[repo-hygiene] Completado. {len(tasks) - len(errors)}/{len(tasks)} tareas OK")
    if errors:
        for tid, msg in errors:
            print(f"  ✗ {tid}: {msg}", file=sys.stderr)
        sys.exit(1)

    if args.export_feed or config.get("feed", {}).get("export_on_complete", False):
        try:
            sys.path.insert(0, str(HYGIENE_DIR / "scripts"))
            from export_feed import maybe_export_after_run
            maybe_export_after_run(REPO_ROOT)
        except Exception as exc:
            print(f"[WARN] Feed export failed: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
