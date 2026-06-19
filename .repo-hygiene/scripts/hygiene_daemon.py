#!/usr/bin/env python3
import os
import sys
import time
import json
import ctypes
import argparse
import subprocess
from pathlib import Path
from datetime import datetime, timezone
import yaml

HYGIENE_DIR = Path(__file__).parent.parent
CONFIG_FILE = HYGIENE_DIR / "config.yaml"
REPORTS_DIR = HYGIENE_DIR / ".reports"
QUEUE_FILE  = REPORTS_DIR / "review_queue.json"
LOG_FILE    = REPORTS_DIR / "daemon.log"

# Ctypes estructura para inactividad en Windows
class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

def load_config() -> dict:
    if not CONFIG_FILE.exists():
        return {}
    with open(CONFIG_FILE, encoding='utf-8') as f:
        try:
            return yaml.safe_load(f) or {}
        except Exception:
            return {}

def log_message(message: str):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_line = f"[{timestamp}] {message}\n"
    print(log_line.strip())
    try:
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(log_line)
    except Exception:
        pass

def get_idle_time_seconds() -> float:
    """Retorna los segundos transcurridos desde la última actividad del usuario en Windows."""
    if not sys.platform.startswith("win"):
        # Fallback para no-Windows: simular inactividad de 0 para evitar ejecuciones accidentales
        return 0.0
    lii = LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
        # GetTickCount mide milisegundos desde el arranque
        millis = ctypes.windll.kernel32.GetTickCount() - lii.dwTime
        return max(0.0, millis / 1000.0)
    return 0.0

def is_night_time(start_hour: int, end_hour: int) -> bool:
    """Retorna True si la hora local actual está dentro de la ventana de ejecución nocturna."""
    current_hour = datetime.now().hour
    if start_hour <= end_hour:
        return start_hour <= current_hour < end_hour
    else:
        # Cruza la medianoche (ej: de 22:00 a 6:00)
        return current_hour >= start_hour or current_hour < end_hour

def init_queue():
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    if not QUEUE_FILE.exists():
        with open(QUEUE_FILE, "w", encoding="utf-8") as f:
            json.dump({"pending": [], "completed": []}, f, indent=2)

def write_pid_file():
    PID_FILE = REPORTS_DIR / "daemon.pid"
    try:
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        PID_FILE.write_text(str(os.getpid()))
        import atexit
        def remove_pid():
            try:
                if PID_FILE.exists():
                    PID_FILE.unlink()
            except:
                pass
        atexit.register(remove_pid)
    except Exception:
        pass


def populate_system_proposals(queue_data: dict, config: dict):
    """Autocompleta la cola con propuestas del sistema para repositorios que no han sido analizados."""
    hub_config = config.get("hub", {})
    scan_root_rel = hub_config.get("scan_root", "..")
    scan_root = (HYGIENE_DIR / scan_root_rel).resolve()
    exclude_dirs = hub_config.get("exclude_dirs", [])
    
    if not scan_root.exists():
        return
        
    detected_repos = []
    try:
        for child in scan_root.iterdir():
            if not child.is_dir():
                continue
            if child.name in exclude_dirs or child.name.startswith('.'):
                continue
            if (child / ".git").exists():
                detected_repos.append(child)
    except Exception as e:
        log_message(f"[ERROR] No se pudieron escanear repositorios en {scan_root}: {e}")
        return
        
    existing_repos = set()
    for task in queue_data.get("pending", []):
        existing_repos.add(str(Path(task.get("repo_path")).resolve()))
    for task in queue_data.get("completed", []):
        existing_repos.add(str(Path(task.get("repo_path")).resolve()))
        
    added_any = False
    for repo in detected_repos:
        repo_str = str(repo.resolve())
        if repo_str not in existing_repos:
            new_task = {
                "repo_path": repo_str,
                "task_type": "ai_review",
                "target_file": None,
                "added_by": "system",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": "pending"
            }
            queue_data["pending"].append(new_task)
            log_message(f"[SYSTEM PROPOSAL] Repositorio detectado y propuesto en cola: {repo.name}")
            added_any = True
            
    if added_any:
        # Guardar la cola actualizada de inmediato sin usar save_queue recursivo
        try:
            with open(QUEUE_FILE, "w", encoding="utf-8") as f:
                json.dump(queue_data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            log_message(f"[ERROR] No se pudo guardar la cola: {e}")

def load_queue() -> dict:
    init_queue()
    try:
        with open(QUEUE_FILE, "r", encoding="utf-8-sig") as f:
            queue_data = json.load(f)
    except Exception as e:
        log_message(f"[ERROR] No se pudo leer la cola: {e}")
        queue_data = {"pending": [], "completed": []}
        
    # Si no hay tareas pendientes, rellenamos con las propuestas del sistema
    if not queue_data.get("pending"):
        config = load_config()
        populate_system_proposals(queue_data, config)
        
    return queue_data

def save_queue(queue_data: dict):
    try:
        with open(QUEUE_FILE, "w", encoding="utf-8") as f:
            json.dump(queue_data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log_message(f"[ERROR] No se pudo guardar la cola: {e}")

def run_task(task: dict) -> bool:
    repo_path = task.get("repo_path")
    task_type = task.get("task_type", "static_scan")
    target_file = task.get("target_file")
    
    log_message(f"Iniciando tarea: {task_type} sobre {repo_path}")
    
    cmd = []
    if task_type == "ai_review":
        cmd = [sys.executable, str(HYGIENE_DIR / "scripts" / "ai_reviewer.py"), "--repo", repo_path]
        if target_file:
            cmd.extend(["--file", target_file])
    else:
        # Escaneo estático por defecto con hub_scanner
        cmd = [sys.executable, str(HYGIENE_DIR / "scripts" / "hub_scanner.py"), "--repo", repo_path]
        
    try:
        # Ejecutar el script correspondiente
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
        if result.returncode == 0:
            log_message(f"✓ Tarea completada exitosamente para {repo_path}")
            return True
        else:
            log_message(f"✗ Tarea falló con código {result.returncode} para {repo_path}")
            log_message(f"Detalle error: {result.stderr[:200]}")
            return False
    except Exception as e:
        log_message(f"[ERROR] Excepción corriendo tarea: {e}")
        return False

def process_queue() -> bool:
    """Busca una tarea en la cola y la ejecuta. Retorna True si procesó algo."""
    queue_data = load_queue()
    pending = queue_data.get("pending", [])
    
    if not pending:
        return False
        
    task = pending.pop(0)
    task["started_at"] = datetime.now(timezone.utc).isoformat()
    
    success = run_task(task)
    
    task["finished_at"] = datetime.now(timezone.utc).isoformat()
    task["status"] = "success" if success else "failed"
    
    queue_data["completed"].append(task)
    save_queue(queue_data)

    if success and task.get("repo_path"):
        try:
            sys.path.insert(0, str(HYGIENE_DIR / "scripts"))
            from export_feed import maybe_export_after_run
            maybe_export_after_run(Path(task["repo_path"]).resolve())
        except Exception as exc:
            log_message(f"[WARN] Feed export failed: {exc}")

    return True

def main():
    parser = argparse.ArgumentParser(description="repo-hygiene background scheduler daemon")
    parser.add_argument("--test-idle", action="store_true", help="Probar cálculo de inactividad de Windows y salir")
    parser.add_argument("--run-once", action="store_true", help="Procesar la primera tarea en la cola inmediatamente sin esperar inactividad")
    args = parser.parse_args()
    
    if args.test_idle:
        idle_secs = get_idle_time_seconds()
        print(f"Sistema Operativo: {sys.platform}")
        print(f"Inactividad del usuario (Idle Time): {idle_secs:.2f} segundos ({idle_secs/60:.2f} minutos)")
        is_night = is_night_time(4, 9)
        print(f"¿Es horario nocturno forzado (04:00 - 09:00)? {'SÍ' if is_night else 'NO'}")
        return
        
    if args.run_once:
        log_message("Modo run-once: Procesando primera tarea de la cola inmediatamente.")
        processed = process_queue()
        if not processed:
            log_message("No había tareas pendientes en la cola.")
        return
        
    # Arrancar demonio normal
    log_message("=== Arrancando Daemon de repo-hygiene ===")
    write_pid_file()
    init_queue()
    
    while True:
        # Cargar configuración fresca en cada ciclo para permitir cambios en caliente
        config = load_config()
        daemon_config = config.get("daemon", {})
        
        idle_threshold = daemon_config.get("idle_threshold_seconds", 900)
        poll_interval = daemon_config.get("poll_interval_seconds", 15)
        night_start = daemon_config.get("night_start_hour", 4)
        night_end = daemon_config.get("night_end_hour", 9)
        
        idle_secs = get_idle_time_seconds()
        is_night = is_night_time(night_start, night_end)
        
        # Leer cola rápida
        queue_data = load_queue()
        pending = queue_data.get("pending", [])
        
        if pending:
            # Si hay tareas, comprobar inactividad u horario nocturno
            # Para evitar interferir si el usuario interactúa activamente a las 5 AM, requerimos
            # que al menos no se esté moviendo el ratón/teclado en los últimos 10 segundos
            is_active_input = (idle_secs < 10.0)
            
            if (idle_secs >= idle_threshold or is_night) and not is_active_input:
                log_message(f"Condiciones de ejecución cumplidas (Inactividad: {idle_secs/60:.1f}m, Nocturno: {is_night})")
                process_queue()
            else:
                # Esperando condiciones
                pass
        
        time.sleep(poll_interval)

if __name__ == "__main__":
    main()
