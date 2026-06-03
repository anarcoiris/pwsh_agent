import os
import sys
import json
import asyncio
from typing import Any
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.prompt import Prompt
from rich.status import Status
from prompt_toolkit import PromptSession
from rich.table import Table
import pyfiglet

import agent
from audit import get_audit

console = Console(force_terminal=True)

class AgentConsole:
    def __init__(self):
        self.agent = agent.ReActAgent()
        self.is_running = True
        self._active_status = None
        console_cfg = self.agent.config.get("console", {})
        self.submit_binding = console_cfg.get("submit_binding", "ctrl-enter")

    @staticmethod
    def _normalize_command(cmd: str) -> str:
        """Map slash aliases (/help) to Prompt.ask choice names."""
        c = (cmd or "").strip().lower()
        aliases = {
            "/help": "help",
            "/exit": "exit",
            "/quit": "exit",
            "/mission": "mission",
            "/chat": "chat",
            "/status": "status",
            "/new": "new",
            "/audit": "audit",
            "/cancel": "cancel",
        }
        return aliases.get(c, c)

    def display_banner(self):
        """Displays a beautiful, hacker-style retro ANSI banner."""
        banner_text = pyfiglet.figlet_format("Pulse Agent", font="slant")
        console.print(f"[bold cyan]{banner_text}[/bold cyan]")
        console.print("[bold white]Streamlined PowerShell ReAct Console Command Center[/bold white]")
        console.print(f"[dim]Ollama Target: {self.agent.base_url} | Model: {self.agent.default_model}[/dim]")
        console.print("-" * 65)
        console.print(
            "[yellow]Type a command: [bold]help[/bold], [bold]mission[/bold], [bold]chat[/bold], "
            "[bold]status[/bold], [bold]exit[/bold], … (aliases: /help, /exit)[/yellow]\n"
        )

    def handle_agent_event(self, event_type: str, data: Any):
        """Callback to handle real-time cognitive events and render them to screen."""
        if event_type == "AGENT_STATUS":
            if self._active_status:
                self._active_status.update(f"[bold blue]{data}[/bold blue]")

        elif event_type == "AGENT_THOUGHT":
            if self._active_status:
                self._active_status.stop()
            # Only display non-sequential thoughts here (sequential already ANSI-rendered)
            if not str(data).startswith("[Parser reflection"):
                console.print(f"\n[bold purple]🧠 Thought:[/bold purple] {data}\n")
            else:
                console.print(f"[dim italic]{data}[/dim italic]")
            if self._active_status:
                self._active_status.start()

        elif event_type == "AGENT_TEXT":
            # Stop the spinner to print text
            if self._active_status:
                self._active_status.stop()
            console.print(f"\n[bold purple]🧠 Reasoning:[/bold purple]\n{data}\n")
            if self._active_status:
                self._active_status.start()

        elif event_type == "AGENT_TOOL_CALL":
            if self._active_status:
                self._active_status.stop()
            tool = data.get("tool")
            args = data.get("args", {})
            args_str = json.dumps(args)
            if len(args_str) > 80:
                args_str = args_str[:77] + "..."
            console.print(f"[bold cyan]⚒ Tool Call:[/bold cyan] [green]{tool}[/green] [dim]({args_str})[/dim]")
            if self._active_status:
                self._active_status.start()

        elif event_type == "AGENT_TOOL_RESULT":
            if self._active_status:
                self._active_status.stop()
            tool = data.get("tool")
            result = data.get("result", {})
            
            # Smart formatting based on result success
            if isinstance(result, dict):
                success = result.get("success", True)
                if not success:
                    console.print(f"[bold red]❌ Tool Error ({tool}):[/bold red] {result.get('error', 'Unknown')}")
                elif tool in ("host_exec", "run_script"):
                    exit_code = result.get("exit_code")
                    duration = result.get("duration_ms")
                    color = "green" if exit_code == 0 else "red"
                    console.print(f"[bold {color}]✔ Tool Result ({tool}):[/bold {color}] Exit Code: {exit_code} ({duration}ms)")
                    
                    stdout = result.get("stdout", "").strip()
                    stderr = result.get("stderr", "").strip()
                    if stdout:
                        # Limit large stdout preview
                        if len(stdout) > 800:
                            stdout = stdout[:800] + "\n[dim]... (output truncated) ...[/dim]"
                        console.print(f"[dim]{stdout}[/dim]")
                    if stderr:
                        console.print(f"[bold red]Stderr Output:[/bold red]\n[red]{stderr}[/red]")
                else:
                    res_str = json.dumps({k: v for k, v in result.items() if k != "success"}, indent=2, default=str)
                    if len(res_str) > 800:
                        res_str = res_str[:800] + "\n[dim]... (output truncated) ...[/dim]"
                    if res_str.strip() == "{}":
                        console.print(f"[bold green]✔ Tool Result ({tool}):[/bold green] Success")
                    else:
                        console.print(f"[bold green]✔ Tool Result ({tool}):[/bold green]\n[dim]{res_str}[/dim]")
            else:
                res_str = str(result)
                if len(res_str) > 800:
                    res_str = res_str[:800] + "\n[dim]... (output truncated) ...[/dim]"
                console.print(f"[bold green]✔ Tool Result ({tool}):[/bold green]\n[dim]{res_str}[/dim]")

            if self._active_status:
                self._active_status.start()

        elif event_type == "MISSION_COMPLETED":
            if self._active_status:
                self._active_status.stop()
            console.print("\n[bold green]✅ Mission complete — compiling final report...[/bold green]")

        elif event_type == "ERROR":
            if self._active_status:
                self._active_status.stop()
            console.print(f"\n[bold red]❌ ERROR:[/bold red] {data}\n")
            if self._active_status:
                self._active_status.start()

    def _build_prompt_session(self) -> tuple[PromptSession, bool, str]:
        """Configure the prompt session from the console submit_binding setting.

        Returns (session, multiline, submit_hint).
        """
        from prompt_toolkit.key_binding import KeyBindings

        binding = (self.submit_binding or "ctrl-enter").lower()

        if binding == "enter":
            # Single-line submit on Enter — no multiline buffer.
            return PromptSession(), False, "Press Enter to submit"

        if binding == "ctrl-enter":
            kb = KeyBindings()
            try:
                @kb.add("c-j")  # Ctrl+Enter sends c-j in most terminals
                def _submit(event):
                    event.current_buffer.validate_and_handle()
                return PromptSession(key_bindings=kb), True, "Press Ctrl+Enter to submit"
            except Exception:
                console.print(
                    "[yellow]⚠ Ctrl+Enter binding not supported — using Esc then Enter.[/yellow]"
                )
                return PromptSession(), True, "Press Esc then Enter to submit"

        # "esc-enter" (and any unknown value) → prompt_toolkit multiline default.
        return PromptSession(), True, "Press Esc then Enter to submit"

    async def run_repl(self):
        """Active menu and command-driven console loop."""
        self.display_banner()
        prompt_session, multiline_mode, submit_hint = self._build_prompt_session()
        
        while self.is_running:
            # Build colorized badges for active settings
            mode_color = "bold green" if self.agent.network_mode == "SANDBOX" else "bold red"
            mode_badge = f"[[{mode_color}]{self.agent.network_mode}[/{mode_color}]]"
            spec_badge = f"[bold magenta]{self.agent.active_specialist.upper()}[/bold magenta]"
            
            prompt_text = f"PulseLab {mode_badge} ({spec_badge}) >"
            
            try:
                raw_cmd = Prompt.ask(
                    prompt_text,
                    choices=["mission", "chat", "specialist", "toggle", "audit", "cancel", "status", "new", "session", "help", "exit"],
                )
                cmd = self._normalize_command(raw_cmd)
                
                if cmd == "exit":
                    self.is_running = False
                    console.print("[yellow]Exiting Pulse Console. Goodbye![/yellow]")
                    break
                
                elif cmd == "new":
                    new_id = self.agent.new_session()
                    console.clear()
                    self.display_banner()
                    console.print(
                        f"[bold green]✔ New session started: {new_id}[/bold green] "
                        "[dim](prior work: 'session list' / 'session pick <id>' — handoff summaries only)[/dim]\n"
                    )

                elif cmd == "session":
                    sub = Prompt.ask(
                        "Session command",
                        choices=["list", "pick", "clear"],
                        default="list",
                    )
                    if sub == "list":
                        from core.session_handoff import list_sealed_handoffs
                        from core.session_paths import load_active_session_id

                        active = load_active_session_id()
                        console.print(f"[bold]Active session:[/bold] {active}")
                        handoffs = list_sealed_handoffs(limit=10)
                        if not handoffs:
                            console.print("[dim]No sealed handoffs yet.[/dim]")
                        else:
                            t = Table(title="Sealed handoffs", border_style="cyan")
                            t.add_column("Session", style="cyan")
                            t.add_column("Domain")
                            t.add_column("Summary")
                            for h in handoffs:
                                t.add_row(
                                    str(h.get("session_id", "")),
                                    str(h.get("domain", "")),
                                    str(h.get("summary", ""))[:60],
                                )
                            console.print(t)
                    elif sub == "pick":
                        sid = Prompt.ask("Session id to load (YYYYMMDD_HHMMSS)")
                        self.agent.select_prior_session(sid.strip())
                        console.print(f"[green]Prior handoff selected: {sid}[/green]\n")
                    elif sub == "clear":
                        self.agent.clear_prior_session_selection()
                        console.print("[green]Prior session selection cleared.[/green]\n")
                    
                elif cmd == "status":
                    self.show_status()
                    
                elif cmd == "help":
                    self.show_help()
                    
                elif cmd == "toggle":
                    # Toggle logical network mode
                    old_mode = self.agent.network_mode
                    self.agent.network_mode = "HOST" if old_mode == "SANDBOX" else "SANDBOX"
                    self.agent._init_system_prompt()  # Refresh prompt context
                    icon = "🛡️" if self.agent.network_mode == "SANDBOX" else "🔥"
                    console.print(f"\n{icon} [bold]Network Mode Toggled:[/bold] {old_mode} -> [bold magenta]{self.agent.network_mode}[/bold magenta]")
                    if self.agent.network_mode == "HOST":
                        console.print("[bold red]WARNING: Local system commands have direct network permission scope.[/bold red]\n")
                    else:
                        console.print("[bold green]SAFE: Local actions run with standard sandbox assumptions.[/bold green]\n")

                elif cmd == "audit":
                    self.show_audit()

                elif cmd == "cancel":
                    self.agent.request_cancel()
                    console.print("[bold yellow]⏹ Cancellation signal sent — mission will stop after the current step.[/bold yellow]\n")

                elif cmd == "specialist":
                    # Swap cognitive mode
                    active_spec = Prompt.ask(
                        "Select Specialist Mode", 
                        choices=["lead", "network", "re", "exploit"], 
                        default="lead"
                    )
                    self.agent.active_specialist = active_spec
                    self.agent._init_system_prompt()  # Dynamically reload prompt
                    console.print(f"[bold green]✔ Persona shifted successfully to: {active_spec.upper()}[/bold green]\n")
                    
                elif cmd == "mission":
                    console.print(f"[bold green]Enter mission objective[/bold green] [dim]({submit_hint})[/dim]")
                    objective = await prompt_session.prompt_async(">>> ", multiline=multiline_mode)
                    if not objective.strip():
                        continue
                    
                    self._active_status = console.status("[bold blue]Agent processing mission...[/bold blue]")
                    self._active_status.start()
                    
                    try:
                        response = await self.agent.run_mission(objective.strip(), self.handle_agent_event)
                    finally:
                        if self._active_status:
                            self._active_status.stop()
                            self._active_status = None

                    # Format and print the final synthesis response
                    console.print(Panel(
                        Text(response, style="white"),
                        title="[bold green]Pulse Agent Mission Response[/bold green]",
                        border_style="green",
                        expand=False
                    ))
                    console.print()

                elif cmd == "chat":
                    console.print(f"[bold green]Chat with Agent[/bold green] [dim]({submit_hint})[/dim]")
                    message = await prompt_session.prompt_async(">>> ", multiline=multiline_mode)
                    if not message.strip():
                        continue

                    self._active_status = console.status("[bold blue]Agent is thinking...[/bold blue]")
                    self._active_status.start()

                    try:
                        response = await self.agent.chat_turn(message.strip(), self.handle_agent_event)
                    finally:
                        if self._active_status:
                            self._active_status.stop()
                            self._active_status = None

                    console.print(Panel(
                        Text(response, style="white"),
                        title="[bold blue]Agent[/bold blue]",
                        border_style="blue",
                        expand=False
                    ))
                    console.print()

            except KeyboardInterrupt:
                console.print("\n[yellow]Session interrupted. Type 'exit' to close cleanly.[/yellow]")
            except Exception as e:
                console.print(f"[bold red]Fatal error during loop: {e}[/bold red]")

    def show_audit(self):
        audit = get_audit()
        result = audit.verify()
        # Integrity summary
        color = "green" if result["tampered"] == 0 else "bold red"
        console.print(Panel(
            f"[{color}]Total: {result['total']} | Valid: {result['valid']} | Tampered: {result['tampered']}[/{color}]",
            title="[bold yellow]Audit Trail Integrity[/bold yellow]",
            border_style="yellow"
        ))
        if result["tampered_entries"]:
            console.print("[bold red]Tampered entries at:[/bold red]", result["tampered_entries"])

        # Recent entries table
        recent = audit.recent(15)
        if recent:
            t = Table(title="Recent Audit Entries (Last 15)", border_style="dim")
            t.add_column("Time", style="dim", no_wrap=True)
            t.add_column("Tool", style="cyan")
            t.add_column("Status", style="white")
            t.add_column("Mode", style="magenta")
            t.add_column("Duration", style="yellow")
            for e in recent:
                ts = e.get("timestamp", "")[:19].replace("T", " ")
                status_style = "green" if e.get("status") == "success" else "red"
                t.add_row(
                    ts,
                    e.get("method", ""),
                    f"[{status_style}]{e.get('status', '')}[/{status_style}]",
                    e.get("network_mode", ""),
                    f"{e.get('duration_ms', '-')}ms",
                )
            console.print(t)
        else:
            console.print("[dim]No audit entries recorded today.[/dim]")

    def show_status(self):
        audit_summary = get_audit().verify()
        thought_audit = self.agent.thinking_engine.get_audit_history()
        table = Table(title="Console Status Summary", border_style="cyan")
        table.add_column("Property", style="magenta")
        table.add_column("Value", style="white")
        table.add_row("Ollama Host",              self.agent.base_url)
        table.add_row("Active Model",             self.agent.default_model)
        table.add_row("Session ID",               self.agent.session_id)
        table.add_row("Specialist Mode",          self.agent.active_specialist.upper())
        table.add_row("Safety Badge",             self.agent.network_mode)
        table.add_row("Message Turns",            str(len(self.agent.messages)))
        table.add_row("Max Steps Budget",         str(self.agent.max_steps))
        table.add_row("Thoughts Used/Budget",
                      f"{thought_audit['totalProcessed']}/{thought_audit['maxBudget']}")
        table.add_row("Thought Branches",         str(thought_audit["branches"]) or "none")
        table.add_row("Audit Entries Today",      str(audit_summary["total"]))
        table.add_row("Audit Integrity",
                      "[green]OK[/green]" if audit_summary["tampered"] == 0 else "[bold red]TAMPERED[/bold red]")
        console.print(table)

    def show_help(self):
        help_table = Table(title="Interactive Command Guide", border_style="yellow")
        help_table.add_column("Command", style="cyan")
        help_table.add_column("Description", style="white")
        help_table.add_row("mission",    "Provide a primary multi-turn goal for the agent to execute autonomously.")
        help_table.add_row("chat",       "Converse with the agent — single-turn with optional tool use.")
        help_table.add_row("specialist", "Shift cognitive overlay (Lead, Network, RE, Exploit Dev).")
        help_table.add_row("toggle",     "Toggle safety badge ([SANDBOX] vs [HOST]).")
        help_table.add_row("cancel",     "Signal a running mission to stop after the current step.")
        help_table.add_row("audit",      "View today's HMAC-signed audit trail and verify integrity.")
        help_table.add_row("new",        "Start a new session (seals outgoing handoff; no auto prior load).")
        help_table.add_row("session",    "list / pick / clear prior session handoff summaries.")
        help_table.add_row("status",     "Show configuration, thought budget, and audit metrics.")
        help_table.add_row("exit",       "Terminate the session safely.")
        console.print(help_table)

if __name__ == "__main__":
    app = AgentConsole()
    # Check if Windows platform and set appropriate asyncio event policy if needed
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(app.run_repl())
