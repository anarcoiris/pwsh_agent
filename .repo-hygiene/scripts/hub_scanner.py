#!/usr/bin/env python3
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
import yaml
from collections import defaultdict

HYGIENE_DIR = Path(__file__).parent.parent
CONFIG_FILE = HYGIENE_DIR / "config.yaml"

def load_config() -> dict:
    if not CONFIG_FILE.exists():
        print(f"[ERROR] No se encontró {CONFIG_FILE}", file=sys.stderr)
        sys.exit(1)
    with open(CONFIG_FILE, encoding='utf-8') as f:
        return yaml.safe_load(f)

# colors for console
class Colors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'

def get_repos(scan_root: Path, exclude_dirs: list) -> list:
    repos = []
    if not scan_root.exists():
        return repos
    for child in scan_root.iterdir():
        if not child.is_dir():
            continue
        if child.name in exclude_dirs or child.name.startswith('.'):
            continue
        repos.append(child)
    return repos

def check_git_duplicates(repos: list) -> list:
    print(f"\n{Colors.OKBLUE}=== Auditoría Git y Duplicados ==={Colors.ENDC}")
    url_to_repos = defaultdict(list)
    for repo in repos:
        git_dir = repo / ".git"
        if not git_dir.exists():
            continue
        try:
            result = subprocess.run(["git", "config", "--get", "remote.origin.url"], cwd=repo, capture_output=True, text=True)
            if result.returncode == 0 and result.stdout.strip():
                url = result.stdout.strip()
                url_to_repos[url].append(repo.name)
        except Exception:
            pass
    
    findings = []
    for url, repo_names in url_to_repos.items():
        if len(repo_names) > 1:
            msg = f"[DUPLICADO] El remote '{url}' está clonado en múltiples directorios: {', '.join(repo_names)}"
            print(f"{Colors.WARNING}  {msg}{Colors.ENDC}")
            findings.append(msg)
    if not findings:
        print(f"{Colors.OKGREEN}  ✓ No se encontraron repositorios clonados múltiples veces.{Colors.ENDC}")
    return findings

def check_hardcoded_paths(repos: list, patterns: list) -> list:
    print(f"\n{Colors.OKBLUE}=== Escáner de Rutas Hardcodeadas ==={Colors.ENDC}")
    findings = []
    exts = {'.py', '.js', '.ts', '.json', '.yml', '.yaml', '.ps1', '.bat', '.md', '.txt', ''}
    regexes = [re.compile(re.escape(p), re.IGNORECASE) for p in patterns]
    
    for repo in repos:
        for root, dirs, files in os.walk(repo):
            dirs[:] = [d for d in dirs if d not in ('.git', '.venv', 'venv', 'node_modules', '__pycache__', 'dist', 'build')]
            for file in files:
                filepath = Path(root) / file
                if filepath.suffix.lower() not in exts and file not in ('Caddyfile', 'Dockerfile', 'services.json'):
                    continue
                try:
                    lines = filepath.read_text(encoding='utf-8', errors='ignore').splitlines()
                    for i, line in enumerate(lines):
                        for reg in regexes:
                            if reg.search(line):
                                rel_path = filepath.relative_to(repo.parent)
                                msg = f"[HARDCODED] {rel_path}:{i+1} contiene ruta absoluta sensible."
                                print(f"{Colors.WARNING}  {msg}{Colors.ENDC}")
                                findings.append(msg)
                                break
                except Exception:
                    pass
    if not findings:
        print(f"{Colors.OKGREEN}  ✓ No se detectaron rutas absolutas sensibles.{Colors.ENDC}")
    return findings

def check_venv_hygiene(repos: list, heavy_pkgs: list) -> list:
    print(f"\n{Colors.OKBLUE}=== Validación de Entornos Virtuales ==={Colors.ENDC}")
    findings = []
    for repo in repos:
        venvs = [repo / '.venv', repo / 'venv', repo / '.venv312', repo / '.venv310']
        found_venv = None
        for v in venvs:
            if v.exists() and v.is_dir():
                found_venv = v
                break
        
        req_files = [repo / 'requirements.txt', repo / 'pyproject.toml']
        has_reqs = any(r.exists() for r in req_files)
        
        if has_reqs and not found_venv:
            msg = f"[VENV] {repo.name} tiene dependencias pero no parece tener un entorno virtual local (.venv)."
            print(f"{Colors.WARNING}  {msg}{Colors.ENDC}")
            findings.append(msg)
            
        for req in req_files:
            if req.exists() and req.name == 'requirements.txt':
                try:
                    content = req.read_text(encoding='utf-8', errors='ignore')
                    for line in content.splitlines():
                        pkg = line.split('==')[0].split('>=')[0].strip()
                        if pkg in heavy_pkgs:
                            msg = f"[HEAVY-PKG] {repo.name}/{req.name} incluye paquete pesado '{pkg}'. ¿Usar servicio centralizado (Docker)?"
                            print(f"{Colors.FAIL}  {msg}{Colors.ENDC}")
                            findings.append(msg)
                except:
                    pass
    if not findings:
        print(f"{Colors.OKGREEN}  ✓ No se detectaron problemas de higiene en venvs.{Colors.ENDC}")
    return findings

def check_docker_hygiene(repos: list) -> list:
    print(f"\n{Colors.OKBLUE}=== Validación de Docker Compose ==={Colors.ENDC}")
    findings = []
    all_ports = defaultdict(list)
    
    for repo in repos:
        compose_files = list(repo.glob('docker-compose*.yml')) + list(repo.glob('docker-compose*.yaml'))
        for cf in compose_files:
            try:
                with open(cf, 'r', encoding='utf-8') as f:
                    data = yaml.safe_load(f)
                if not data or not isinstance(data, dict):
                    continue
                if 'version' in data:
                    msg = f"[DOCKER] {repo.name}/{cf.name} usa la clave 'version' obsoleta en la raíz."
                    print(f"{Colors.WARNING}  {msg}{Colors.ENDC}")
                    findings.append(msg)
                
                services = data.get('services', {})
                for s_name, s_data in services.items():
                    for p in s_data.get('ports', []):
                        if isinstance(p, str):
                            host_port = p.split(':')[0]
                            all_ports[host_port].append(f"{repo.name}:{s_name}")
                        elif isinstance(p, dict) and 'published' in p:
                            all_ports[str(p['published'])].append(f"{repo.name}:{s_name}")
                            
                    for v in s_data.get('volumes', []):
                        if isinstance(v, str):
                            local_path = v.split(':')[0]
                            if local_path.startswith('./') or local_path.startswith('../'):
                                abs_path = (cf.parent / local_path).resolve()
                                if not abs_path.exists():
                                    msg = f"[DOCKER] {repo.name}/{cf.name} ({s_name}) monta un volumen local que NO EXISTE: {local_path}"
                                    print(f"{Colors.FAIL}  {msg}{Colors.ENDC}")
                                    findings.append(msg)
            except Exception:
                pass
                
    for port, svcs in all_ports.items():
        if len(svcs) > 1:
            msg = f"[PUERTOS] Conflicto! El host port {port} es publicado por múltiples servicios: {', '.join(svcs)}"
            print(f"{Colors.FAIL}  {msg}{Colors.ENDC}")
            findings.append(msg)
            
    if not findings:
        print(f"{Colors.OKGREEN}  ✓ Las configuraciones Docker parecen correctas.{Colors.ENDC}")
    return findings

def check_caddy_integration(repos: list, caddy_hub_path: Path) -> list:
    print(f"\n{Colors.OKBLUE}=== Integración con Caddy Hub ==={Colors.ENDC}")
    findings = []
    if not caddy_hub_path.exists():
        print(f"  [INFO] No se encontró caddy-hub en {caddy_hub_path}. Se omite.")
        return findings
        
    services_json = caddy_hub_path / "services.json"
    if not services_json.exists():
        print(f"  [INFO] No se encontró services.json en {caddy_hub_path}. Se omite.")
        return findings
        
    try:
        data = json.loads(services_json.read_text(encoding='utf-8'))
        ref_roots = set()
        for v in data.get('venvs', []):
            if 'path' in v: ref_roots.add(v['path'].replace('\\', '/').split('/')[0])
            
        for s in data.get('services', []):
            if 'cwd' in s: ref_roots.add(s['cwd'].replace('\\', '/').split('/')[0])
            elif 'composeFile' in s: ref_roots.add(s['composeFile'].replace('\\', '/').split('/')[0])
            
        for b in data.get('builds', []):
            if 'cwd' in b: ref_roots.add(b['cwd'].replace('\\', '/').split('/')[0])
            elif 'path' in b: ref_roots.add(b['path'].replace('\\', '/').split('/')[0])
        
        all_referenced_paths = ref_roots
        
        for ref_path in all_referenced_paths:
            abs_p = caddy_hub_path.parent / ref_path
            if not abs_p.exists():
                msg = f"[CADDY] services.json referencia una ruta que no existe: {ref_path}"
                print(f"{Colors.FAIL}  {msg}{Colors.ENDC}")
                findings.append(msg)
                
        for repo in repos:
            if repo.name not in all_referenced_paths and repo.name != caddy_hub_path.name:
                msg = f"[CADDY] {repo.name} está activo pero no referenciado en caddy-hub services.json."
                print(f"{Colors.WARNING}  {msg}{Colors.ENDC}")
                findings.append(msg)
    except Exception as e:
        msg = f"[CADDY] Error leyendo services.json: {e}"
        print(f"{Colors.FAIL}  {msg}{Colors.ENDC}")
        findings.append(msg)
        
    if not findings:
        print(f"{Colors.OKGREEN}  ✓ Caddy Hub está sincronizado.{Colors.ENDC}")
    return findings

def check_garbage(repos: list) -> list:
    print(f"\n{Colors.OKBLUE}=== Limpieza de Archivos Basura ==={Colors.ENDC}")
    findings = []
    garbage_patterns = ['debug.log', '$null', '*.wav', '*.log.gz', 'temp.*', '*.tmp']
    for repo in repos:
        for pat in garbage_patterns:
            for garbage_file in repo.glob(pat):
                msg = f"[GARBAGE] Archivo basura detectado en raíz: {repo.name}/{garbage_file.name}"
                print(f"{Colors.WARNING}  {msg}{Colors.ENDC}")
                findings.append(msg)
    if not findings:
        print(f"{Colors.OKGREEN}  ✓ No se encontraron archivos basura en la raíz.{Colors.ENDC}")
    return findings

def check_file_bloat_and_binaries(repos: list) -> list:
    print(f"\n{Colors.OKBLUE}=== Escáner de Binarios, Logs y Archivos Pesados ==={Colors.ENDC}")
    findings = []
    
    bin_extensions = {'.exe', '.dll', '.so', '.dylib', '.class', '.o', '.bin', '.pkl', '.h5', '.pt', '.pth'}
    
    for repo in repos:
        for root, dirs, files in os.walk(repo):
            dirs[:] = [d for d in dirs if d not in ('.git', '.venv', 'venv', 'node_modules', '__pycache__')]
            for file in files:
                filepath = Path(root) / file
                
                # Check file size (> 50MB)
                try:
                    size_mb = filepath.stat().st_size / (1024 * 1024)
                    if size_mb > 50:
                        rel_path = filepath.relative_to(repo.parent)
                        msg = f"[BLOAT] Archivo excesivamente grande ({size_mb:.1f} MB): {rel_path}"
                        print(f"{Colors.WARNING}  {msg}{Colors.ENDC}")
                        findings.append(msg)
                except Exception:
                    pass
                
                # Check unignored logs
                if file.endswith('.log'):
                    rel_path = filepath.relative_to(repo.parent)
                    msg = f"[LOGS] Archivo de log detectado en árbol de trabajo: {rel_path}"
                    print(f"{Colors.WARNING}  {msg}{Colors.ENDC}")
                    findings.append(msg)
                
                # Check compiled binaries
                if filepath.suffix.lower() in bin_extensions:
                    rel_path = filepath.relative_to(repo.parent)
                    msg = f"[BINARY] Posible archivo binario o pesos de IA en árbol fuente: {rel_path}"
                    print(f"{Colors.WARNING}  {msg}{Colors.ENDC}")
                    findings.append(msg)

    if not findings:
        print(f"{Colors.OKGREEN}  ✓ No se detectaron binarios innecesarios, logs sueltos ni archivos sobredimensionados.{Colors.ENDC}")
    return findings

def check_activity(repos: list) -> list:
    print(f"\n{Colors.OKBLUE}=== Auditoría de Actividad (Recomendaciones de Archivo) ==={Colors.ENDC}")
    findings = []
    for repo in repos:
        if not (repo / ".git").exists():
            continue
        try:
            result = subprocess.run(["git", "log", "-1", "--format=%ai"], cwd=repo, capture_output=True, text=True)
            if result.returncode == 0 and result.stdout.strip():
                date_str = result.stdout.strip()[:10]
                last_commit_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                days_ago = (datetime.now(timezone.utc) - last_commit_date).days
                if days_ago > 180:
                    msg = f"[INACTIVO] {repo.name} no tiene commits desde hace {days_ago} días. ¿Mover a Archive_Legacy?"
                    print(f"{Colors.WARNING}  {msg}{Colors.ENDC}")
                    findings.append(msg)
        except Exception:
            pass
    if not findings:
        print(f"{Colors.OKGREEN}  ✓ Todos los repositorios tienen actividad reciente (< 180 días).{Colors.ENDC}")
    return findings

def main():
    if sys.platform.startswith("win"):
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except AttributeError:
            pass

    parser = argparse.ArgumentParser(description="Multi-Repo Hub Scanner for repo-hygiene")
    parser.add_argument("--all", action="store_true", help="Run all checks")
    parser.add_argument("--check", choices=["git", "paths", "venv", "docker", "caddy", "garbage", "bloat", "activity"], 
                        help="Run a specific check")
    parser.add_argument("--root", type=str, help="Override root directory to scan")
    parser.add_argument("--repo", type=str, help="Scan only a specific repository directory (absolute or relative to root/cwd)")
    args = parser.parse_args()

    config = load_config()
    hub_config = config.get("hub", {})
    
    scan_root_rel = hub_config.get("scan_root", "..")
    if args.root:
        scan_root = Path(args.root).resolve()
    else:
        scan_root = (HYGIENE_DIR / scan_root_rel).resolve()
        
    exclude_dirs = hub_config.get("exclude_dirs", [])
    patterns = hub_config.get("sensitive_path_patterns", [])
    heavy_pkgs = hub_config.get("heavyweight_packages", [])
    
    caddy_rel = hub_config.get("caddy_hub_path", "../caddy-hub")
    caddy_path = (HYGIENE_DIR / caddy_rel).resolve()

    print(f"{Colors.HEADER}==================================================")
    print(f"      HUB SCANNER - REPO-HYGIENE MULTI-REPO")
    print(f"=================================================={Colors.ENDC}")
    print(f"Directorio Raíz: {scan_root}")
    if not args.repo:
        print(f"Exclusiones: {', '.join(exclude_dirs)}\n")
    else:
        print(f"Foco en repositorio: {args.repo}\n")

    if args.repo:
        repo_path = Path(args.repo)
        if not repo_path.is_absolute():
            # Probar relativo al CWD
            cwd_repo = repo_path.resolve()
            if cwd_repo.exists() and cwd_repo.is_dir():
                repos = [cwd_repo]
            else:
                # Probar relativo al scan_root
                scan_repo = (scan_root / args.repo).resolve()
                if scan_repo.exists() and scan_repo.is_dir():
                    repos = [scan_repo]
                else:
                    print(f"{Colors.FAIL}[ERROR] No se encontró la ruta del repositorio: {args.repo}{Colors.ENDC}", file=sys.stderr)
                    sys.exit(1)
        else:
            if repo_path.exists() and repo_path.is_dir():
                repos = [repo_path]
            else:
                print(f"{Colors.FAIL}[ERROR] No se encontró la ruta del repositorio: {args.repo}{Colors.ENDC}", file=sys.stderr)
                sys.exit(1)
    else:
        repos = get_repos(scan_root, exclude_dirs)
    
    all_findings = []
    
    if args.all or args.check == "git" or not any([args.all, args.check]):
        all_findings.extend(check_git_duplicates(repos))
    if args.all or args.check == "paths" or not any([args.all, args.check]):
        all_findings.extend(check_hardcoded_paths(repos, patterns))
    if args.all or args.check == "venv" or not any([args.all, args.check]):
        all_findings.extend(check_venv_hygiene(repos, heavy_pkgs))
    if args.all or args.check == "docker" or not any([args.all, args.check]):
        all_findings.extend(check_docker_hygiene(repos))
    if args.all or args.check == "caddy" or not any([args.all, args.check]):
        all_findings.extend(check_caddy_integration(repos, caddy_path))
    if args.all or args.check == "garbage" or not any([args.all, args.check]):
        all_findings.extend(check_garbage(repos))
    if args.all or args.check == "bloat" or not any([args.all, args.check]):
        all_findings.extend(check_file_bloat_and_binaries(repos))
    if args.all or args.check == "activity" or not any([args.all, args.check]):
        all_findings.extend(check_activity(repos))
        
    print(f"\n{Colors.HEADER}==================================================")
    print(f"      RESUMEN DEL ESCANEO")
    print(f"=================================================={Colors.ENDC}")
    print(f"Total de hallazgos: {len(all_findings)}")
    
    report_dir = HYGIENE_DIR / ".reports"
    report_dir.mkdir(exist_ok=True)
    report_file = report_dir / "hub_hygiene_report.md"
    
    md_content = f"# 🧹 Hub Scanner Report\n\n**Fecha:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    md_content += f"**Directorio:** `{scan_root}`\n\n"
    
    if not all_findings:
        md_content += "🎉 **Excelente higiene.** No se detectaron problemas en el ecosistema.\n"
    else:
        md_content += f"Se encontraron **{len(all_findings)}** problemas o sugerencias:\n\n"
        for finding in all_findings:
            md_content += f"- {finding}\n"
            
    report_file.write_text(md_content, encoding='utf-8')
    print(f"\nInforme guardado en: {report_file.relative_to(scan_root)}")

if __name__ == "__main__":
    main()
