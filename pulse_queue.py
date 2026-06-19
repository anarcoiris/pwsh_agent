#!/usr/bin/env python3
"""Pulse Queue — interactive CLI for unified work orchestration.

Usage:
  py -3.10 pulse_queue.py              # interactive REPL
  py -3.10 pulse_queue.py daemon       # background orchestrator only
  py -3.10 pulse_queue.py run-once     # run one eligible job now
  py -3.10 pulse_queue.py add          # quick add wizard
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.table import Table

console = Console()


def _banner() -> None:
    console.print(Panel(
        "[bold cyan]Pulse Queue[/bold cyan]\n"
        "Cola unificada: misiones · hygiene · editorial · schedules idle/noche",
        border_style="cyan",
    ))


def _list_jobs(include_done: bool = False) -> None:
    from core.work_queue import list_jobs, queue_stats

    stats = queue_stats()
    if stats:
        parts = " | ".join(f"{k}={v}" for k, v in sorted(stats.items()))
        console.print(f"[dim]Stats: {parts}[/dim]")

    jobs = list_jobs(include_done=include_done)
    if not jobs:
        console.print("[dim]Cola vacía.[/dim]")
        return

    t = Table(title="Work Queue", border_style="cyan")
    t.add_column("ID", style="cyan")
    t.add_column("Pri", style="yellow")
    t.add_column("Type", style="magenta")
    t.add_column("Status")
    t.add_column("Next", style="dim")
    t.add_column("Idle", style="dim")
    t.add_column("Title")
    for j in jobs:
        sc = {"pending": "green", "running": "blue", "paused": "yellow", "done": "dim"}.get(
            j.get("status", ""), "white"
        )
        idle = j.get("requires_idle_seconds")
        idle_s = f"{idle}s" if idle else "-"
        t.add_row(
            j["id"],
            str(j.get("priority", 50)),
            j.get("job_type", ""),
            f"[{sc}]{j.get('status', '')}[/{sc}]",
            (j.get("next_run_at") or "")[:19],
            idle_s,
            (j.get("title") or "")[:55],
        )
    console.print(t)


def _wizard_add() -> None:
    from core.work_queue import enqueue_job

    kind = Prompt.ask(
        "Tipo de trabajo",
        choices=["mission", "hygiene_review", "hygiene_scan", "editorial"],
        default="mission",
    )
    priority = int(Prompt.ask("Prioridad (0-100)", default="50"))
    when = Prompt.ask(
        "Cuándo ejecutar",
        choices=["now", "idle", "night", "idle_or_night", "cron"],
        default="idle_or_night",
    )

    requires_idle = None
    night_start = night_end = None
    cron_expr = None

    if when == "idle":
        requires_idle = int(Prompt.ask("Segundos idle mínimos", default="900"))
    elif when == "night":
        night_start = int(Prompt.ask("Hora inicio noche", default="22"))
        night_end = int(Prompt.ask("Hora fin noche", default="7"))
    elif when == "idle_or_night":
        requires_idle = int(Prompt.ask("Segundos idle mínimos", default="900"))
        night_start = int(Prompt.ask("Hora inicio noche (bypass idle)", default="22"))
        night_end = int(Prompt.ask("Hora fin noche", default="7"))
    elif when == "cron":
        cron_expr = Prompt.ask("Expresión cron (5 campos)", default="0 3 * * *")

    if kind == "mission":
        text = Prompt.ask("Texto de misión")
        specialist = Prompt.ask("Specialist", default="workspace")
        mode = Prompt.ask("Network mode", choices=["SANDBOX", "HOST"], default="SANDBOX")
        payload = {"mission_text": text, "specialist": specialist, "network_mode": mode}
        job_type = "pwsh_mission"
    elif kind == "hygiene_review":
        repo = Prompt.ask("Ruta del repo")
        payload = {"repo_path": repo, "task_type": "ai_review"}
        job_type = "hygiene_review"
    elif kind == "hygiene_scan":
        repo = Prompt.ask("Ruta del repo")
        payload = {"repo_path": repo}
        job_type = "hygiene_scan"
    else:
        console.print("[yellow]Editorial: stub — usa mission por ahora.[/yellow]")
        return

    jid = enqueue_job(
        job_type,
        payload,
        priority=priority,
        cron_expr=cron_expr,
        requires_idle_seconds=requires_idle,
        night_start_hour=night_start,
        night_end_hour=night_end,
    )
    console.print(f"[green]Encolado:[/green] {jid}")


def _status() -> None:
    from core.idle_detect import get_idle_time_seconds, is_night_time
    from core.work_queue import queue_stats
    import yaml
    from core.runtime_paths import app_root

    cfg = {}
    p = app_root() / "config.yaml"
    if p.is_file():
        with open(p, encoding="utf-8") as f:
            cfg = (yaml.safe_load(f) or {}).get("orchestrator") or {}

    idle = get_idle_time_seconds()
    ns = cfg.get("night_start_hour", 22)
    ne = cfg.get("night_end_hour", 7)
    night = is_night_time(ns, ne)

    t = Table(title="Orchestrator Status", border_style="cyan")
    t.add_column("Key", style="magenta")
    t.add_column("Value")
    t.add_row("Idle (s)", f"{idle:.0f} ({idle/60:.1f} min)")
    t.add_row("Night window", f"{ns}:00–{ne}:00 → {'YES' if night else 'no'}")
    t.add_row("Idle threshold", str(cfg.get("idle_threshold_seconds", 900)))
    for k, v in sorted(queue_stats().items()):
        t.add_row(f"jobs.{k}", str(v))
    console.print(t)


def _repl() -> None:
    _banner()
    while True:
        try:
            cmd = Prompt.ask(
                "pulse-queue",
                choices=["list", "add", "pause", "resume", "cancel", "run-once", "status", "daemon", "help", "exit"],
                default="list",
            )
        except (KeyboardInterrupt, EOFError):
            break

        if cmd == "exit":
            break
        if cmd == "list":
            _list_jobs(include_done=Confirm.ask("Incluir completados?", default=False))
        elif cmd == "add":
            _wizard_add()
        elif cmd == "pause":
            from core.work_queue import pause_job
            jid = Prompt.ask("Job ID")
            console.print("[green]OK[/green]" if pause_job(jid.strip()) else "[red]No encontrado[/red]")
        elif cmd == "resume":
            from core.work_queue import resume_job
            jid = Prompt.ask("Job ID")
            console.print("[green]OK[/green]" if resume_job(jid.strip()) else "[red]No encontrado[/red]")
        elif cmd == "cancel":
            from core.work_queue import cancel_job
            jid = Prompt.ask("Job ID")
            console.print("[green]OK[/green]" if cancel_job(jid.strip()) else "[red]No encontrado[/red]")
        elif cmd == "run-once":
            asyncio.run(_run_once(force=True))
        elif cmd == "status":
            _status()
        elif cmd == "daemon":
            asyncio.run(_daemon())
        elif cmd == "help":
            console.print(
                "[cyan]list[/] · [cyan]add[/] · pause/resume/cancel · "
                "[cyan]run-once[/] · [cyan]status[/] · [cyan]daemon[/]"
            )


async def _run_once(force: bool = False) -> None:
    from core.orchestrator import orchestrator_tick

    result = await orchestrator_tick(agent=None, force=force)
    if result.get("ran"):
        console.print(f"[green]Ejecutado[/green] {result.get('job_id')} ({result.get('job_type')}) — {result.get('reason')}")
    else:
        console.print(f"[dim]Sin ejecución:[/dim] {result.get('reason', result.get('error', 'unknown'))}")


async def _daemon() -> None:
    from core.orchestrator import orchestrator_loop

    console.print("[cyan]Daemon orchestrator — Ctrl+C para salir[/cyan]")
    try:
        await orchestrator_loop(agent=None, interval_s=15)
    except KeyboardInterrupt:
        console.print("[yellow]Detenido.[/yellow]")


def main() -> int:
    parser = argparse.ArgumentParser(description="Pulse Queue orchestrator CLI")
    parser.add_argument("command", nargs="?", default="repl", choices=["repl", "add", "list", "run-once", "daemon", "status"])
    args = parser.parse_args()

    if args.command == "repl":
        _repl()
    elif args.command == "add":
        _banner()
        _wizard_add()
    elif args.command == "list":
        _banner()
        _list_jobs(include_done="--all" in sys.argv)
    elif args.command == "run-once":
        asyncio.run(_run_once(force=True))
    elif args.command == "daemon":
        asyncio.run(_daemon())
    elif args.command == "status":
        _banner()
        _status()
    return 0


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    raise SystemExit(main())
