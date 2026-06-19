#!/usr/bin/env python3
import os
import sys
import json
import fnmatch
import argparse
import ast
import re
from pathlib import Path
import requests
import yaml
from datetime import datetime, timezone

from ollama_client import chat as ollama_chat, unload_model
from review_concurrency import (
    cluster_unload_policy,
    resolve_concurrency,
    run_cluster_batch,
)

HYGIENE_DIR = Path(__file__).parent.parent
CONFIG_FILE = HYGIENE_DIR / "config.yaml"


def _estimate_tokens(text: str) -> int:
    """Conservative ~3 chars/token for code-heavy prompts."""
    return max(1, len(text) // 3)


def _user_char_budget(system_prompt: str, num_ctx: int, reserve_output: int = 2048) -> int:
    """Max user-message chars that fit in num_ctx alongside system + generation."""
    margin = 128
    prompt_token_budget = num_ctx - reserve_output - margin
    user_tokens = prompt_token_budget - _estimate_tokens(system_prompt)
    return max(3000, user_tokens * 3)


def _truncate_user_message(
    user_message: str, system_prompt: str, num_ctx: int, reserve_output: int = 2048
) -> tuple[str, bool]:
    budget = _user_char_budget(system_prompt, num_ctx, reserve_output)
    if len(user_message) <= budget:
        return user_message, False
    return (
        user_message[:budget] + "\n\n# [...TRUNCADO: prompt excedía num_ctx; recortado en cliente...]\n",
        True,
    )


def load_review_prompt(name: str) -> str:
    path = HYGIENE_DIR / "agents" / f"{name}.md"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _model_profile(block: dict, defaults: dict) -> dict:
    return {
        "model": block.get("model", defaults["model"]),
        "num_ctx": block.get("num_ctx", defaults["num_ctx"]),
        "reserve_output_tokens": block.get(
            "reserve_output_tokens", defaults.get("reserve_output_tokens", 2048)
        ),
        "max_context_chars": block.get(
            "max_context_chars", defaults.get("max_context_chars", 12000)
        ),
        "temperature": block.get("temperature", defaults.get("temperature", 0.2)),
    }


def resolve_pipeline(ai_config: dict) -> tuple[str, dict, dict, bool]:
    """Returns (pipeline_mode, planner_profile, coder_profile, unload_after)."""
    unload = ai_config.get("unload_after_call", True)
    legacy = {
        "model": ai_config.get("model", "qwen2.5-coder:7b-instruct"),
        "num_ctx": ai_config.get("num_ctx", 8192),
        "reserve_output_tokens": ai_config.get("reserve_output_tokens", 2048),
        "max_context_chars": ai_config.get("max_context_chars", 12000),
        "temperature": 0.1,
    }
    pipeline = ai_config.get("pipeline", "dual")
    if pipeline == "coder_only":
        return "coder_only", legacy, legacy, unload

    planner_defaults = {
        **legacy,
        "model": "vibethinker:3b",
        "num_ctx": 16384,
        "reserve_output_tokens": 3072,
        "max_context_chars": 28000,
        "temperature": 0.3,
    }
    planner = _model_profile(ai_config.get("planner", {}), planner_defaults)
    coder = _model_profile(ai_config.get("coder", {}), legacy)
    return "dual", planner, coder, unload


def _extract_cluster_guidance(plan_text: str, cluster_idx: int) -> str:
    """Pull the planner section for one cluster; fallback to plan excerpt."""
    pattern = (
        rf"(?:^|\n)#{1,3}\s*Cluster\s*{cluster_idx}\b.*?"
        rf"(?=\n#{1,3}\s*Cluster\s*\d+\b|\Z)"
    )
    match = re.search(pattern, plan_text, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(0).strip()
    return plan_text[:2500].strip()


def _build_routing_message(
    repo_name: str,
    clusters: list[list[str]],
    import_graph: dict[str, set[str]],
    det_findings: list[str],
    domains: list[dict],
    tree_str: str,
) -> str:
    lines = [
        f"Repositorio: **{repo_name}**",
        "",
        "## Árbol (resumen)",
        "```",
        tree_str[:3000],
        "```",
        "",
        "## Clusters (metadatos; el coder analizará el código)",
    ]
    for idx, files in enumerate(clusters, 1):
        local_edges = {
            f: sorted(import_graph.get(f, set()) & set(files)) for f in files
        }
        lines.append(f"\n### Cluster {idx} — {len(files)} archivo(s)")
        lines.append(f"Archivos: `{', '.join(files)}`")
        if any(local_edges.values()):
            lines.append(f"Dependencias internas: `{json.dumps(local_edges)}`")
    if domains:
        lines.append("\n## Dominios declarados (domains.yaml)")
        lines.append(json.dumps(domains, indent=2))
    lines.append("\n## Hallazgos deterministas (Paso 0)")
    if det_findings:
        lines.extend(f"- {f}" for f in det_findings)
    else:
        lines.append("- Ninguno")
    lines.append(
        "\n## Tarea\n"
        "Elabora un **plan de revisión** en Markdown (sin JSON). "
        "Para cada cluster escribe una sección `## Cluster N` con:\n"
        "- **Prioridad**: alta | media | baja\n"
        "- **Foco**: qué aspectos de diseño priorizar\n"
        "- **Riesgos**: señales a vigilar\n\n"
        "Opcional: una sección `## Orden recomendado` si el orden de revisión importa."
    )
    return "\n".join(lines)


def _build_cluster_user_message(
    idx: int,
    total: int,
    cluster_files: list[str],
    *,
    import_graph: dict[str, set[str]],
    code_files: dict[str, str],
    effective_cluster_chars: int,
    review_plan: str,
    det_findings: list[str],
    active_system: str,
    coder_num_ctx: int,
    coder_reserve: int,
) -> tuple[str, bool]:
    cluster_graph = {f: list(import_graph[f] & set(cluster_files)) for f in cluster_files}
    user_message = f"=== CHUNK DE VECINDAD {idx}/{total} ===\n"
    if review_plan:
        guidance = _extract_cluster_guidance(review_plan, idx)
        user_message += f"=== FOCO DEL PLANIFICADOR ===\n{guidance}\n\n"
    user_message += f"Subgrafo de dependencias local:\n{json.dumps(cluster_graph, indent=2)}\n\n"
    user_message += "=== ARCHIVOS INCLUIDOS EN ESTE CHUNK ===\n"
    for filepath in cluster_files:
        content = code_files[filepath]
        if len(content) > effective_cluster_chars:
            content = content[:effective_cluster_chars] + "\n\n# [...TRUNCADO...]"
        user_message += f"\n--- Archivo: `{filepath}` ---\n{content}\n"
    local_findings = [f for f in det_findings if any(file_in_f in f for file_in_f in cluster_files)]
    if local_findings:
        user_message += "\n=== ALERTAS DETERMINISTAS (PASO 0) ===\n"
        for f in local_findings:
            user_message += f"- {f}\n"
    user_message += (
        "\nRealiza el censo taxonómico y la auditoría de diseño de estos archivos."
    )
    return _truncate_user_message(user_message, active_system, coder_num_ctx, coder_reserve)

# Colores para consola
class Colors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'

def load_config() -> dict:
    if not CONFIG_FILE.exists():
        print(f"[ERROR] No se encontró {CONFIG_FILE}", file=sys.stderr)
        sys.exit(1)
    with open(CONFIG_FILE, encoding='utf-8') as f:
        return yaml.safe_load(f)

def is_excluded(path: Path, repo_path: Path, exclude_patterns: list) -> bool:
    try:
        rel_path = path.relative_to(repo_path)
    except ValueError:
        return True
    rel_str = str(rel_path).replace('\\', '/')
    
    for pattern in exclude_patterns:
        if fnmatch.fnmatch(rel_str, pattern):
            return True
        # Comprobar si alguna de las partes de la ruta coincide con el patrón limpio
        clean_pat = pattern.rstrip('/*').strip('/')
        for part in rel_path.parts:
            if fnmatch.fnmatch(part, pattern) or fnmatch.fnmatch(part, clean_pat):
                return True
    return False

def get_project_tree(repo_path: Path, exclude_patterns: list) -> list[str]:
    """Genera un listado en árbol de la estructura de directorios y archivos del proyecto."""
    lines = []
    
    def _walk(directory: Path, prefix: str = ""):
        try:
            items = sorted(directory.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
        except Exception:
            return
            
        items = [item for item in items if not is_excluded(item, repo_path, exclude_patterns)]
        
        for i, item in enumerate(items):
            is_last = (i == len(items) - 1)
            connector = "└── " if is_last else "├── "
            lines.append(f"{prefix}{connector}{item.name}")
            
            if item.is_dir():
                new_prefix = prefix + ("    " if is_last else "│   ")
                # Limitar profundidad del árbol a 4 niveles para evitar saturación de tokens
                if len(new_prefix) // 4 <= 4:
                    _walk(item, new_prefix)
                    
    _walk(repo_path)
    return lines

def extract_python_skeleton(file_path: Path) -> str:
    """Extrae el esqueleto estructural de un archivo Python (importaciones, clases, funciones y docstrings) usando AST."""
    try:
        content = file_path.read_text(encoding='utf-8', errors='ignore')
        tree = ast.parse(content)
    except Exception as e:
        return f"# [Error parsing AST for {file_path.name}: {e}]\n"

    class SkeletonTransformer:
        def visit_module(self, node: ast.Module):
            new_body = []
            doc = ast.get_docstring(node)
            if doc:
                new_body.append(ast.Expr(value=ast.Constant(value=doc)))
            for child in node.body:
                if isinstance(child, (ast.Import, ast.ImportFrom)):
                    new_body.append(child)
                elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    new_body.append(self.visit_function(child))
                elif isinstance(child, ast.ClassDef):
                    new_body.append(self.visit_class(child))
                elif isinstance(child, (ast.Assign, ast.AnnAssign)):
                    new_body.append(child)
            return ast.Module(body=new_body, type_ignores=[])

        def visit_class(self, node: ast.ClassDef) -> ast.ClassDef:
            new_body = []
            doc = ast.get_docstring(node)
            if doc:
                new_body.append(ast.Expr(value=ast.Constant(value=doc)))
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    new_body.append(self.visit_function(child))
                elif isinstance(child, ast.ClassDef):
                    new_body.append(self.visit_class(child))
                elif isinstance(child, (ast.Assign, ast.AnnAssign)):
                    new_body.append(child)
            if not new_body:
                new_body.append(ast.Pass())
            return ast.ClassDef(
                name=node.name,
                bases=node.bases,
                keywords=node.keywords,
                decorator_list=node.decorator_list,
                body=new_body
            )

        def visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> ast.FunctionDef | ast.AsyncFunctionDef:
            new_body = []
            doc = ast.get_docstring(node)
            if doc:
                new_body.append(ast.Expr(value=ast.Constant(value=doc)))
            new_body.append(ast.Pass())
            cls = ast.AsyncFunctionDef if isinstance(node, ast.AsyncFunctionDef) else ast.FunctionDef
            return cls(
                name=node.name,
                args=node.args,
                decorator_list=node.decorator_list,
                returns=node.returns,
                body=new_body
            )

    transformer = SkeletonTransformer()
    new_tree = transformer.visit_module(tree)
    try:
        ast.fix_missing_locations(new_tree)
        return ast.unparse(new_tree)
    except Exception as e:
        return f"# [Error unparsing AST for {file_path.name}: {e}]\n"

def collect_code_files(repo_path: Path, exclude_patterns: list, max_size_kb: int, target_file_rel: str = None) -> dict[str, str]:
    """Carga el contenido o esqueleto de los archivos de código clave para enviarlos como contexto."""
    code_files = {}
    valid_extensions = {'.py', '.js', '.ts', '.tsx', '.jsx', '.go', '.rs', '.java', '.cs', '.yaml', '.yml', 'Dockerfile', 'Caddyfile'}
    
    for root, dirs, files in os.walk(repo_path):
        # Filtrar directorios in situ
        dirs[:] = [d for d in dirs if not is_excluded(Path(root) / d, repo_path, exclude_patterns)]
        
        for file in files:
            file_path = Path(root) / file
            if is_excluded(file_path, repo_path, exclude_patterns):
                continue
                
            # Verificar extensión o nombres especiales
            if file_path.suffix.lower() not in valid_extensions and file_path.name not in ('Dockerfile', 'Caddyfile', 'services.json'):
                continue
                
            try:
                rel_path = file_path.relative_to(repo_path)
                rel_path_str = str(rel_path).replace('\\', '/')
                
                # Si es el archivo objetivo de auditoría, se carga completo
                if target_file_rel and rel_path_str == target_file_rel.replace('\\', '/'):
                    content = file_path.read_text(encoding='utf-8', errors='ignore')
                    code_files[rel_path_str] = content
                    continue
                
                # Si es un archivo Python, extraemos el esqueleto con AST
                if file_path.suffix.lower() == '.py':
                    content = extract_python_skeleton(file_path)
                    code_files[rel_path_str] = content
                else:
                    # Para otros lenguajes (JS, TS, Go, Rust, etc.), leemos las primeras 100 líneas
                    # Excepción para archivos de configuración pequeños (yaml, Dockerfile, Caddyfile, services.json) que se leen enteros si son pequeños.
                    size_kb = file_path.stat().st_size / 1024
                    if size_kb > max_size_kb:
                        continue
                        
                    if file_path.suffix.lower() in ('.yaml', '.yml') or file_path.name in ('Dockerfile', 'Caddyfile', 'services.json'):
                        content = file_path.read_text(encoding='utf-8', errors='ignore')
                    else:
                        # Leer primeras 100 líneas
                        lines = []
                        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                            for _ in range(100):
                                line = f.readline()
                                if not line:
                                    break
                                lines.append(line)
                        content = "".join(lines)
                        if len(lines) == 100:
                            content += "\n# [... Contenido restante omitido para optimizar contexto ...]\n"
                    
                    code_files[rel_path_str] = content
            except Exception:
                pass
    return code_files

# ─────────────────────────────────────────────────────────────
#  Fase 0: Censo y Reglas Deterministas (Grafo + Regex + AST)
# ─────────────────────────────────────────────────────────────

def get_python_imports(file_content: str) -> list[tuple[int, str | None, list[str]]]:
    imports = []
    try:
        tree = ast.parse(file_content)
    except Exception:
        return []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for name in node.names:
                imports.append((0, None, [name.name]))
        elif isinstance(node, ast.ImportFrom):
            names = [name.name for name in node.names]
            imports.append((node.level, node.module, names))
    return imports

def resolve_python_import(importer_rel_path: str, level: int, module: str | None, names: list[str], code_files: set[str]) -> list[str]:
    resolved = []
    def check_candidate(candidate_base: str) -> str | None:
        c_py = f"{candidate_base}.py"
        c_init = f"{candidate_base}/__init__.py"
        if c_py in code_files:
            return c_py
        if c_init in code_files:
            return c_init
        return None

    if level > 0:
        importer_parts = Path(importer_rel_path).parent.parts
        if level <= len(importer_parts):
            parent_parts = importer_parts[:len(importer_parts) - level + 1]
            parent_dir = "/".join(parent_parts)
            if module:
                module_path = module.replace('.', '/')
                candidate = f"{parent_dir}/{module_path}" if parent_dir else module_path
                res = check_candidate(candidate)
                if res:
                    resolved.append(res)
                else:
                    for name in names:
                        candidate_with_name = f"{candidate}/{name}"
                        res_name = check_candidate(candidate_with_name)
                        if res_name:
                            resolved.append(res_name)
            else:
                for name in names:
                    candidate = f"{parent_dir}/{name}" if parent_dir else name
                    res = check_candidate(candidate)
                    if res:
                        resolved.append(res)
    else:
        modules_to_try = []
        if module:
            modules_to_try.append(module)
        for name in names:
            if module:
                modules_to_try.append(f"{module}.{name}")
            else:
                modules_to_try.append(name)
                
        for mod in modules_to_try:
            mod_path = mod.replace('.', '/')
            res = check_candidate(mod_path)
            if res:
                resolved.append(res)
            else:
                parts = mod.split('.')
                for i in range(len(parts), 0, -1):
                    subpath = "/".join(parts[:i])
                    res_sub = check_candidate(subpath)
                    if res_sub:
                        resolved.append(res_sub)
                        break
    return list(set(resolved))

def get_js_ts_imports(file_content: str) -> list[str]:
    pattern = re.compile(r'(?:import|require|from)\s*[(\'"]([@\w./-]+)[\'")]')
    return pattern.findall(file_content)

def resolve_js_ts_import(importer_rel_path: str, import_str: str, code_files: set[str]) -> str | None:
    if not (import_str.startswith('./') or import_str.startswith('../')):
        return None
    importer_dir = Path(importer_rel_path).parent
    try:
        resolved_rel = os.path.normpath(os.path.join(str(importer_dir), import_str)).replace('\\', '/')
    except Exception:
        return None
    candidates = [
        resolved_rel,
        f"{resolved_rel}.ts",
        f"{resolved_rel}.js",
        f"{resolved_rel}.tsx",
        f"{resolved_rel}.jsx",
        f"{resolved_rel}/index.ts",
        f"{resolved_rel}/index.js",
        f"{resolved_rel}/index.tsx",
        f"{resolved_rel}/index.jsx"
    ]
    for cand in candidates:
        if cand in code_files:
            return cand
    return None

def build_import_graph(code_files: dict[str, str]) -> dict[str, set[str]]:
    import_graph = {filepath: set() for filepath in code_files.keys()}
    code_files_set = set(code_files.keys())
    
    for filepath, content in code_files.items():
        ext = Path(filepath).suffix.lower()
        if ext == '.py':
            imports_data = get_python_imports(content)
            for level, module, names in imports_data:
                resolved = resolve_python_import(filepath, level, module, names, code_files_set)
                for res in resolved:
                    if res != filepath:
                        import_graph[filepath].add(res)
        elif ext in ('.js', '.ts', '.tsx', '.jsx'):
            import_strings = get_js_ts_imports(content)
            for imp in import_strings:
                res = resolve_js_ts_import(filepath, imp, code_files_set)
                if res and res != filepath:
                    import_graph[filepath].add(res)
    return import_graph

def load_repo_rules(repo_path: Path) -> dict:
    rules = {}
    for folder in (repo_path / "config", repo_path):
        cfg_file = folder / "rules.yaml"
        if cfg_file.exists():
            try:
                with open(cfg_file, encoding='utf-8') as f:
                    data = yaml.safe_load(f)
                    if data and isinstance(data, dict):
                        rules.update(data.get("rules", {}))
                        break
            except Exception:
                pass
    return rules

def load_repo_domains(repo_path: Path) -> list[dict]:
    domains = []
    for folder in (repo_path / "config", repo_path):
        cfg_file = folder / "domains.yaml"
        if cfg_file.exists():
            try:
                with open(cfg_file, encoding='utf-8') as f:
                    data = yaml.safe_load(f)
                    if data and isinstance(data, dict):
                        domains = data.get("domains", [])
                        break
            except Exception:
                pass
    return domains

def is_log_file(fp: Path) -> bool:
    name_l = fp.name.lower()
    suffix_l = fp.suffix.lower()
    if suffix_l in ('.py', '.js', '.ts', '.tsx', '.jsx', '.go', '.rs', '.java', '.cs', '.yaml', '.yml', '.md', '.html', '.css'):
        return False
    if suffix_l in ('.log', '.out', '.err'):
        return True
    if re.search(r'\blog(?:s)?\b', name_l) or name_l.endswith('.log') or '_log' in name_l or '-log' in name_l:
        return True
    parts_l = [p.lower() for p in fp.parts]
    if ('logs' in parts_l or 'log' in parts_l) and suffix_l in ('.txt', '.csv', '.json', '.data', '.tsv'):
        return True
    return False

def check_ast_rules(content: str, filepath: str, rules: dict) -> list[str]:
    local_findings = []
    try:
        tree = ast.parse(content)
    except Exception:
        return []
        
    banned_imports = rules.get("banned_imports", [])
    max_function_lines = rules.get("max_function_lines")
    
    for node in ast.walk(tree):
        if banned_imports:
            if isinstance(node, ast.Import):
                for name in node.names:
                    for banned in banned_imports:
                        if name.name == banned or name.name.startswith(banned + '.'):
                            local_findings.append(f"[BANNED-IMPORT] {filepath} importa el módulo prohibido '{name.name}'.")
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    for banned in banned_imports:
                        full_import = f"{node.module}"
                        if full_import == banned or full_import.startswith(banned + '.'):
                            local_findings.append(f"[BANNED-IMPORT] {filepath} importa de un módulo prohibido '{full_import}'.")
                        for alias in node.names:
                            full_alias = f"{node.module}.{alias.name}"
                            if full_alias == banned or full_alias.startswith(banned + '.'):
                                local_findings.append(f"[BANNED-IMPORT] {filepath} importa el elemento prohibido '{full_alias}'.")
                                
        if max_function_lines is not None:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                num_lines = getattr(node, 'end_lineno', node.lineno) - node.lineno + 1
                if num_lines > max_function_lines:
                    local_findings.append(f"[FUNC-TOO-LONG] {filepath}:{node.lineno} la función/método '{node.name}' tiene {num_lines} líneas (máximo permitido: {max_function_lines}).")
                    
    return local_findings

def run_deterministic_checks(repo_path: Path, code_files: dict[str, str], config: dict, rules: dict) -> list[str]:
    findings = []
    
    # 1. HARDCODED PATHS DETECTION
    sensitive_patterns = config.get("hub", {}).get("sensitive_path_patterns", [])
    if not sensitive_patterns:
        sensitive_patterns = [r'C:\\Users\\soyko', r'c:\\Users\\soyko']
    regexes = [re.compile(re.escape(p), re.IGNORECASE) for p in sensitive_patterns]
    
    for filepath, content in code_files.items():
        for i, line in enumerate(content.splitlines(), 1):
            for reg in regexes:
                if reg.search(line):
                    findings.append(f"[HARDCODE] {filepath}:{i} contiene ruta absoluta sensible.")
                    break
        # Run AST checks on python files
        if filepath.endswith('.py'):
            findings.extend(check_ast_rules(content, filepath, rules))

    # 2. REQUIREMENTS BLOAT & MISSING REQUIREMENTS
    all_imports = set()
    for filepath, content in code_files.items():
        if filepath.endswith('.py'):
            for level, module, names in get_python_imports(content):
                if level == 0 and module:
                    all_imports.add(module.split('.')[0])
                for name in names:
                    all_imports.add(name.split('.')[0])

    req_file = repo_path / "requirements.txt"
    if req_file.exists():
        try:
            req_content = req_file.read_text(encoding='utf-8', errors='ignore')
            req_packages = set()
            for line in req_content.splitlines():
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                pkg = re.split(r'==|>=|<=|>|<|;|\s', line)[0].strip()
                if pkg:
                    req_packages.add(pkg)
                    
            pkg_to_import = {
                'pyyaml': 'yaml',
                'beautifulsoup4': 'bs4',
                'scikit-learn': 'sklearn',
                'opencv-python': 'cv2',
                'pytorch-lightning': 'pytorch_lightning',
                'lightning': 'lightning',
                'python-dotenv': 'dotenv'
            }
            
            used_reqs = set()
            for req in req_packages:
                req_lower = req.lower()
                imp_name = pkg_to_import.get(req_lower, req_lower).replace('-', '_')
                if imp_name in all_imports:
                    used_reqs.add(req)
                else:
                    matched = False
                    for imp in all_imports:
                        if imp.startswith(imp_name):
                            matched = True
                            break
                    if matched:
                        used_reqs.add(req)
                        
            unused_reqs = req_packages - used_reqs
            for pkg in sorted(unused_reqs):
                if pkg.lower() not in ('gunicorn', 'setuptools', 'wheel', 'pip', 'pytest', 'black', 'flake8', 'ipykernel', 'jupyter', 'pytest-cov'):
                    findings.append(f"[REQS-BLOAT] Paquete '{pkg}' listado en requirements.txt pero no parece importarse en el código.")
        except Exception as e:
            findings.append(f"[SYSTEM-ERR] Error al procesar requirements.txt: {e}")

    # 3. PYTHON VERSION DETECTION
    pyvenv_cfg = None
    for folder in ('.venv', 'venv', '.venv312', '.venv310'):
        cfg_path = repo_path / folder / "pyvenv.cfg"
        if cfg_path.exists():
            pyvenv_cfg = cfg_path
            break
            
    expected_version = rules.get("python_version")
    if pyvenv_cfg:
        try:
            cfg_content = pyvenv_cfg.read_text(encoding='utf-8', errors='ignore')
            version_match = re.search(r'version\s*=\s*([\d.]+)', cfg_content)
            if version_match:
                venv_version = version_match.group(1)
                if expected_version:
                    if not venv_version.startswith(str(expected_version)):
                        findings.append(f"[PY-VERSION] La versión de Python en el .venv ({venv_version}) no coincide con la esperada ({expected_version}).")
        except Exception as e:
            findings.append(f"[SYSTEM-ERR] Error al leer pyvenv.cfg: {e}")
    else:
        if rules.get("venv_required", True) and any(f.endswith('.py') for f in code_files.keys()) and req_file.exists():
            findings.append(f"[VENV-MISSING] El repositorio tiene archivos Python y dependencias pero carece de un entorno virtual (.venv).")

    # 4. STALE DOCUMENTATION REFERENCE
    readme_file = repo_path / "README.md"
    if readme_file.exists():
        try:
            readme_text = readme_file.read_text(encoding='utf-8', errors='ignore')
            md_links = re.findall(r'\[([^\]]+)\]\(([^)]+)\)', readme_text)
            for text, link in md_links:
                if link.startswith(('http://', 'https://', 'mailto:', '#')):
                    continue
                clean_link = link.split('#')[0].split('?')[0].strip()
                if not clean_link:
                    continue
                target_path = (repo_path / clean_link).resolve()
                try:
                    target_path.relative_to(repo_path)
                    exists = target_path.exists()
                except ValueError:
                    exists = False
                if not exists:
                    findings.append(f"[STALE-DOCS] README.md referencia un enlace local inexistente: `{link}`")
                    
            backtick_paths = re.findall(r'`([\w\-./\\]+\.[\w]{2,4})`', readme_text)
            for path_str in backtick_paths:
                if '/' in path_str or '\\' in path_str:
                    path_str_norm = path_str.replace('\\', '/')
                    if path_str_norm not in code_files and not (repo_path / path_str_norm).exists():
                        findings.append(f"[STALE-DOCS] README.md menciona una ruta de archivo inexistente: `{path_str}`")
        except Exception as e:
            findings.append(f"[SYSTEM-ERR] Error al procesar README.md: {e}")

    # 5. BINARY / LOG LEAKS DETECTION
    binary_extensions = {'.exe', '.dll', '.so', '.dylib', '.bin', '.pyd', '.pyc', '.db', '.sqlite', '.png', '.jpg', '.jpeg', '.gif', '.mp3', '.mp4', '.zip', '.tar.gz', '.tgz', '.rar', '.7z', '.pdf', '.pkl', '.h5', '.pth', '.onnx', '.ckpt'}
    
    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in ('.git', '.venv', 'node_modules', '__pycache__')]
        for file in files:
            file_path = Path(root) / file
            rel_path_str = str(file_path.relative_to(repo_path)).replace('\\', '/')
            if rel_path_str in code_files:
                continue # Skip files already loaded for context review
                
            if file_path.suffix.lower() in binary_extensions:
                findings.append(f"[BINARY-LEAK] Archivo binario/media encontrado en el código fuente: `{rel_path_str}`")
                continue
            if is_log_file(file_path):
                findings.append(f"[BINARY-LEAK] Archivo de log encontrado: `{rel_path_str}`")
                continue
            try:
                size_kb = file_path.stat().st_size / 1024
                if size_kb > 500:
                    findings.append(f"[BINARY-LEAK] Archivo excesivamente grande ({size_kb:.1f} KB) encontrado: `{rel_path_str}`")
            except Exception:
                pass
                
    return findings

# ─────────────────────────────────────────────────────────────
#  BFS 1-Hop Clustering Algorithm
# ─────────────────────────────────────────────────────────────

def cluster_by_neighborhood(graph: dict[str, set[str]], code_files: dict[str, str], max_context_chars: int) -> list[list[str]]:
    undirected_graph = {node: set() for node in graph}
    for u, neighbors in graph.items():
        for v in neighbors:
            if v in undirected_graph:
                undirected_graph[u].add(v)
                undirected_graph[v].add(u)
                
    centrality = {node: len(neighbors) for node, neighbors in undirected_graph.items()}
    sorted_nodes = sorted(centrality.keys(), key=lambda x: centrality[x], reverse=True)
    
    isolated_nodes = [node for node in sorted_nodes if centrality[node] == 0]
    other_nodes = [node for node in sorted_nodes if centrality[node] > 0]
    
    visited = set()
    clusters = []
    
    # Process connected nodes
    for node in other_nodes:
        if node in visited:
            continue
        neighbors = undirected_graph[node] - visited
        cluster_files = [node]
        visited_in_cluster = {node}
        current_size = len(code_files.get(node, ""))
        
        for neigh in sorted(neighbors, key=lambda x: centrality[x], reverse=True):
            file_size = len(code_files.get(neigh, ""))
            if current_size + file_size <= max_context_chars:
                cluster_files.append(neigh)
                visited_in_cluster.add(neigh)
                current_size += file_size
                
        visited.update(visited_in_cluster)
        clusters.append(cluster_files)
        
    # Group isolated nodes together to minimize LLM calls
    current_isolated = []
    current_size = 0
    for node in isolated_nodes:
        if node in visited:
            continue
        file_size = len(code_files.get(node, ""))
        if current_size + file_size > max_context_chars and current_isolated:
            clusters.append(current_isolated)
            current_isolated = []
            current_size = 0
        current_isolated.append(node)
        current_size += file_size
        visited.add(node)
    if current_isolated:
        clusters.append(current_isolated)
        
    return clusters

# ─────────────────────────────────────────────────────────────
#  Core AI Review Process
# ─────────────────────────────────────────────────────────────

def run_ai_review(repo_path: Path, target_file_rel: str = None) -> str:
    config = load_config()
    ai_config = config.get("ai_review", {})
    ollama_url = ai_config.get("ollama_url", "http://localhost:11435").rstrip("/")
    planner_url = (
        ai_config.get("planner_ollama_url")
        or (ai_config.get("planner") or {}).get("ollama_url")
        or ollama_url
    ).rstrip("/")
    coder_url = (
        ai_config.get("coder_ollama_url")
        or (ai_config.get("coder") or {}).get("ollama_url")
        or ollama_url
    ).rstrip("/")
    max_size_kb = ai_config.get("max_file_size_kb", 100)
    exclude_patterns = ai_config.get("exclude_patterns", [])

    pipeline_mode, planner_profile, coder_profile, unload_after = resolve_pipeline(ai_config)
    concurrency = resolve_concurrency(ai_config)
    cluster_unload, unload_after_batch = cluster_unload_policy(unload_after, concurrency)
    rules = load_repo_rules(repo_path)
    domains = load_repo_domains(repo_path)

    print(f"{Colors.OKCYAN}>> Fase 0: Iniciando Censo Determinista de {repo_path.name}...{Colors.ENDC}")
    tree_lines = get_project_tree(repo_path, exclude_patterns)
    tree_str = "\n".join(tree_lines)
    code_files = collect_code_files(repo_path, exclude_patterns, max_size_kb, target_file_rel)
    print(f"   Se cargaron {len(code_files)} archivos de código para análisis.")

    import_graph = build_import_graph(code_files)
    det_findings = run_deterministic_checks(repo_path, code_files, config, rules)
    print(f"   Censo determinista completado. Se hallaron {len(det_findings)} observaciones automáticas.")

    planner_system = load_review_prompt("review_planner")
    coder_system = load_review_prompt("review_coder")

    # Cluster budget follows the coder profile (deep code analysis is VRAM-heavy)
    coder_reserve = coder_profile["reserve_output_tokens"]
    coder_num_ctx = coder_profile["num_ctx"]
    coder_max_chars = coder_profile["max_context_chars"]
    effective_cluster_chars = min(
        coder_max_chars,
        _user_char_budget(coder_system, coder_num_ctx, coder_reserve),
    )
    clusters = cluster_by_neighborhood(import_graph, code_files, effective_cluster_chars)
    print(
        f"   Grafo de dependencias agrupado en {len(clusters)} clusters lógicos "
        f"(presupuesto coder ~{effective_cluster_chars} chars/cluster)."
    )

    if target_file_rel:
        target_norm = target_file_rel.replace("\\", "/")
        clusters = [c for c in clusters if target_norm in c]
        if not clusters:
            print(
                f"{Colors.WARNING}[WARN] El archivo foco '{target_file_rel}' no se encontró "
                f"en los clusters. Analizando todos.{Colors.ENDC}"
            )
            clusters = cluster_by_neighborhood(import_graph, code_files, effective_cluster_chars)
        else:
            print(
                f"   Foco de archivo activo: cluster del archivo foco "
                f"({len(clusters[0])} archivos)."
            )

    review_plan = ""
    if pipeline_mode == "dual":
        print(
            f"\n{Colors.OKCYAN}>> Fase 0.5: Planificador "
            f"({planner_profile['model']}, num_ctx={planner_profile['num_ctx']})...{Colors.ENDC}"
        )
        routing_msg = _build_routing_message(
            repo_path.name, clusters, import_graph, det_findings, domains, tree_str
        )
        routing_msg, plan_trunc = _truncate_user_message(
            routing_msg,
            planner_system,
            planner_profile["num_ctx"],
            planner_profile["reserve_output_tokens"],
        )
        if plan_trunc:
            print(
                f"  {Colors.WARNING}[WARN] Routing: prompt recortado "
                f"(num_ctx={planner_profile['num_ctx']}){Colors.ENDC}"
            )
        try:
            review_plan = ollama_chat(
                planner_url,
                planner_profile,
                planner_system,
                routing_msg,
                unload_after=unload_after,
                client_truncated=plan_trunc,
                metrics_extra={"phase": "planner_routing"},
            )
            print(f"  ✓ Plan de revisión generado ({len(review_plan)} chars).")
        except requests.exceptions.RequestException as e:
            print(f"{Colors.WARNING}[WARN] Planificador falló ({e}); continuando sin plan.{Colors.ENDC}")
            review_plan = ""

    # ── Fase 1: análisis por cluster (coder) ───────────────────
    active_profile = coder_profile if pipeline_mode == "dual" else planner_profile
    active_system = coder_system if pipeline_mode == "dual" else planner_system
    phase1_label = (
        f"Coder ({coder_profile['model']})"
        if pipeline_mode == "dual"
        else f"{active_profile['model']}"
    )
    total_clusters = len(clusters)

    def _run_cluster(idx: int, cluster_files: list[str]) -> tuple[int, list[str], str]:
        user_message, was_truncated = _build_cluster_user_message(
            idx,
            total_clusters,
            cluster_files,
            import_graph=import_graph,
            code_files=code_files,
            effective_cluster_chars=effective_cluster_chars,
            review_plan=review_plan,
            det_findings=det_findings,
            active_system=active_system,
            coder_num_ctx=coder_num_ctx,
            coder_reserve=coder_reserve,
        )
        if was_truncated:
            print(
                f"  {Colors.WARNING}[WARN] Cluster {idx}: prompt recortado "
                f"(num_ctx={coder_num_ctx}){Colors.ENDC}"
            )
        print(
            f"\n{Colors.OKBLUE}=== Fase 1 [{phase1_label}]: "
            f"Cluster {idx}/{total_clusters} ({len(cluster_files)} archivos) ==={Colors.ENDC}"
        )
        try:
            chunk_report = ollama_chat(
                coder_url,
                active_profile,
                active_system,
                user_message,
                unload_after=cluster_unload,
                client_truncated=was_truncated,
                metrics_extra={"phase": "coder_cluster", "cluster": idx, "parallel": concurrency.enabled},
            )
            return idx, cluster_files, chunk_report
        except requests.exceptions.RequestException as e:
            print(f"{Colors.FAIL}[ERROR] Falló Ollama en cluster {idx}: {e}{Colors.ENDC}", file=sys.stderr)
            return idx, cluster_files, f"*Error al procesar cluster {idx} con Ollama ({e}).*"

    chunk_reports = run_cluster_batch(
        [(idx, cf) for idx, cf in enumerate(clusters, 1)],
        _run_cluster,
        concurrency=concurrency,
        on_info=lambda msg: print(f"{Colors.OKCYAN}  {msg}{Colors.ENDC}"),
    )

    if unload_after_batch:
        unload_model(coder_url, active_profile["model"])
        print(f"  {Colors.OKCYAN}✓ Coder descargado de VRAM antes de síntesis.{Colors.ENDC}")

    # ── Fase 2: síntesis ───────────────────────────────────────
    synth_profile = planner_profile if pipeline_mode == "dual" else active_profile
    synth_system = planner_system if pipeline_mode == "dual" else active_system
    synth_num_ctx = synth_profile["num_ctx"]
    synth_reserve = synth_profile["reserve_output_tokens"]

    print(
        f"\n{Colors.OKCYAN}>> Fase 2: Síntesis "
        f"({synth_profile['model']}, num_ctx={synth_num_ctx})...{Colors.ENDC}"
    )

    graph_edges = []
    for u, neighbors in import_graph.items():
        if neighbors:
            graph_edges.append(f"`{u}` importa a: {', '.join(f'`{n}`' for n in neighbors)}")
    graph_str = "\n".join(graph_edges) if graph_edges else "No se detectaron dependencias internas."

    user_message_p2 = f"=== SÍNTESIS GLOBAL: {repo_path.name} ===\n\n"
    if review_plan:
        user_message_p2 += f"=== PLAN DE REVISIÓN (planificador) ===\n{review_plan}\n\n"
    user_message_p2 += f"Estructura de directorios:\n```\n{tree_str[:3000]}\n```\n\n"
    user_message_p2 += f"Grafo de imports:\n{graph_str}\n\n"
    if domains:
        user_message_p2 += f"Dominios (domains.yaml):\n{json.dumps(domains, indent=2)}\n\n"
    user_message_p2 += "=== HALLAZGOS DETERMINISTAS (PASO 0) ===\n"
    user_message_p2 += (
        "\n".join(f"- {f}" for f in det_findings) if det_findings else "- Ninguno"
    )
    user_message_p2 += "\n\n=== REPORTES POR CLUSTER (FASE 1) ===\n"
    per_report_chars = max(
        2000,
        _user_char_budget(synth_system, synth_num_ctx, synth_reserve)
        // max(len(chunk_reports), 1),
    )
    for i, (files, report) in enumerate(chunk_reports, 1):
        body = report if len(report) <= per_report_chars else (
            report[:per_report_chars] + "\n[...truncado...]"
        )
        user_message_p2 += f"\n--- Chunk {i} ({', '.join(files[:3])}...) ---\n{body}\n"
    user_message_p2 += "\nGenera el informe holístico final según tu contrato de síntesis."

    user_message_p2, synth_truncated = _truncate_user_message(
        user_message_p2, synth_system, synth_num_ctx, synth_reserve
    )
    if synth_truncated:
        print(
            f"  {Colors.WARNING}[WARN] Síntesis: prompt recortado en cliente "
            f"(num_ctx={synth_num_ctx}){Colors.ENDC}"
        )

    try:
        synthesis_report = ollama_chat(
            planner_url,
            synth_profile,
            synth_system,
            user_message_p2,
            temperature=synth_profile.get("temperature", 0.2),
            unload_after=unload_after,
            client_truncated=synth_truncated,
            metrics_extra={"phase": "planner_synthesis"},
        )
    except requests.exceptions.RequestException as e:
        print(f"{Colors.FAIL}[ERROR] Síntesis falló: {e}{Colors.ENDC}", file=sys.stderr)
        synthesis_report = (
            f"# Error en síntesis\n\n{e}\n\n## Análisis parciales (Fase 1)\n\n"
        )
        for i, (files, report) in enumerate(chunk_reports, 1):
            synthesis_report += f"### Chunk {i} ({', '.join(files)})\n\n{report}\n\n"

    final_output = "## 📊 Resumen del Censo de Higiene (Paso 0)\n"
    if det_findings:
        final_output += "\n".join(f"- {f}" for f in det_findings)
    else:
        final_output += "- No se detectaron fallos deterministas."
    if review_plan:
        final_output += "\n\n## 🧭 Plan de revisión (planificador)\n\n"
        final_output += review_plan
    final_output += "\n\n---\n\n"
    final_output += synthesis_report
    return final_output

def main():
    if sys.platform.startswith("win"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except AttributeError:
            pass

    parser = argparse.ArgumentParser(description="AI Architecture & Hygiene Reviewer (Ollama 3-Phase)")
    parser.add_argument("--repo", required=True, help="Ruta del repositorio a analizar")
    parser.add_argument("--file", help="Ruta relativa de un archivo específico a auditar con prioridad")
    parser.add_argument("--output", help="Ruta del archivo markdown de salida para el reporte")
    args = parser.parse_args()
    
    repo_path = Path(args.repo).resolve()
    if not repo_path.exists() or not repo_path.is_dir():
        print(f"{Colors.FAIL}[ERROR] El repositorio especificado no existe o no es una carpeta: {args.repo}{Colors.ENDC}", file=sys.stderr)
        sys.exit(1)
        
    print(f"{Colors.HEADER}==================================================")
    print(f"      AI ARCHITECTURE & HYGIENE REVIEWER v3")
    print(f"=================================================={Colors.ENDC}")
    print(f"Repositorio: {repo_path}")
    if args.file:
        print(f"Archivo objetivo: {args.file}")
        
    start_time = datetime.now()
    review_content = run_ai_review(repo_path, args.file)
    end_time = datetime.now()
    
    # Determinar ruta de salida
    reports_dir = HYGIENE_DIR / ".reports" / "ai_review"
    reports_dir.mkdir(parents=True, exist_ok=True)
    
    if args.output:
        output_file = Path(args.output).resolve()
    else:
        safe_name = repo_path.name.replace(" ", "_").replace("(", "").replace(")", "")
        output_file = reports_dir / f"{safe_name}_ai_review.md"
        
    # Escribir el informe
    ai_cfg = load_config().get("ai_review", {})
    pipeline = ai_cfg.get("pipeline", "dual")
    if pipeline == "dual":
        planner_m = ai_cfg.get("planner", {}).get("model", "vibethinker:3b")
        coder_m = ai_cfg.get("coder", {}).get("model", "qwen2.5-coder:7b-instruct")
        model_line = f"**Pipeline:** `dual` · planner `{planner_m}` → coder `{coder_m}`"
    else:
        model_line = f"**Modelo:** `{ai_cfg.get('model', 'qwen2.5-coder:7b-instruct')}`"
    conc = ai_cfg.get("concurrency", {})
    if conc.get("enabled"):
        model_line += f" · **Phase 1 parallel:** {conc.get('max_workers', 3)} workers"
    report_md = f"# 🤖 AI Architecture & Hygiene Review - {repo_path.name}\n\n"
    report_md += f"**Fecha:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    report_md += f"{model_line}\n"
    report_md += f"**Tiempo de análisis:** {((end_time - start_time).total_seconds()):.2f} segundos\n"
    if args.file:
        report_md += f"**Foco de archivo:** `{args.file}`\n"
    report_md += "\n---\n\n"
    report_md += review_content
    
    output_file.write_text(report_md, encoding='utf-8')
    print(f"\n{Colors.OKGREEN}✓ Revisión de arquitectura completada exitosamente!{Colors.ENDC}")
    print(f"Informe guardado en: {output_file.relative_to(HYGIENE_DIR)}")

    try:
        import sys as _sys
        _sys.path.insert(0, str(HYGIENE_DIR / "scripts"))
        from export_feed import maybe_export_after_run
        maybe_export_after_run(repo_path)
    except Exception as exc:
        print(f"{Colors.WARNING}[WARN] Feed export skipped: {exc}{Colors.ENDC}", file=sys.stderr)

if __name__ == "__main__":
    main()
