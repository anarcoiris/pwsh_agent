import os
import re
import sys
import time
import subprocess
from pathlib import Path
from typing import Any, Dict

_WIN_SUBPROCESS_FLAGS = (
    subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
)


def _subprocess_run_kwargs() -> dict:
    # stdin=DEVNULL: under pytest (or any host that replaces std handles with
    # non-inheritable objects) a child that inherits an invalid stdin handle
    # fails on Windows with "[WinError 6] The handle is invalid". Non-interactive
    # tools (tshark, helper scripts) never read stdin, so DEVNULL is always safe.
    kwargs: dict = {"stdin": subprocess.DEVNULL}
    if _WIN_SUBPROCESS_FLAGS:
        kwargs["creationflags"] = _WIN_SUBPROCESS_FLAGS
    return kwargs

# ==========================================
# 1. Sequential Thinking Engine
# ==========================================

class SequentialThinkingEngine:
    """
    Stateful engine for sequential thinking tool logic.
    Maintains thought history and renders beautifully styled steps to stderr.
    """
    def __init__(self, max_thoughts: int = 15):
        self.thought_history = []
        self.max_thoughts = max_thoughts
        self._total_processed = 0

    def process_thought(self, args: dict) -> dict:
        """Process a single thinking step and return status."""
        if self._total_processed >= self.max_thoughts:
            return {
                "thoughtNumber": args.get("thoughtNumber", self._total_processed + 1),
                "totalThoughts": self._total_processed,
                "status": "budget_exceeded",
                "error": f"Thought budget ({self.max_thoughts}) exhausted."
            }

        thought = args.get("thought", "")
        thought_num = args.get("thoughtNumber", 1)
        total_thoughts = args.get("totalThoughts", 1)
        is_revision = args.get("isRevision", False)
        revises_thought = args.get("revisesThought", None)

        self.thought_history.append({
            "thoughtNumber": thought_num,
            "totalThoughts": total_thoughts,
            "thought": thought,
            "isRevision": is_revision,
            "revisesThought": revises_thought
        })
        self._total_processed += 1

        # RENDER thought block with elegant ANSI styling
        BLUE = "\033[94m"
        YELLOW = "\033[93m"
        RESET = "\033[0m"

        color = YELLOW if is_revision else BLUE
        title = f"Thought {thought_num}/{total_thoughts}"
        if is_revision:
            title += f" (Revising #{revises_thought})"

        print(f"\n{color}┌─ {title} {'─' * max(0, 60 - len(title))}{RESET}", file=sys.stderr)
        for line in thought.splitlines():
            print(f"{color}│{RESET} {line}", file=sys.stderr)
        print(f"{color}└{'─' * 63}{RESET}\n", file=sys.stderr)

        return {
            "thoughtNumber": thought_num,
            "totalThoughts": total_thoughts,
            "nextThoughtNeeded": args.get("nextThoughtNeeded", False),
            "status": "success"
        }

    def reset(self):
        self.thought_history.clear()
        self._total_processed = 0


# ==========================================
# 2. Local Windows Shell Execution
# ==========================================

def host_exec(command: str, timeout: int = 120) -> dict:
    """
    Executes a shell command directly on the Windows host using PowerShell.
    
    Args:
        command: The PowerShell command string to run.
        timeout: Maximum execution time in seconds.
    """
    if not command:
        return {"exit_code": -1, "stdout": "", "stderr": "No command provided."}

    start_time = time.time()
    try:
        # Run command via PowerShell NoProfile/NonInteractive.
        # encoding/errors are REQUIRED: text=True alone decodes with the Windows
        # locale codec (cp1252), which raises UnicodeDecodeError on any non-cp1252
        # byte (e.g. 0x81 from a router web page or gzipped/binary output). That
        # killed the subprocess reader thread, left stdout=None, and crashed the
        # agent loop on a downstream .strip(). utf-8 + replace is decode-safe.
        from core.powershell_exec import run_powershell

        raw = run_powershell(command, timeout=timeout)
        result = type("R", (), {})()
        result.returncode = raw.get("exit_code", -1)
        result.stdout = raw.get("stdout", "")
        result.stderr = raw.get("stderr", "") or raw.get("error", "")
        duration = int((time.time() - start_time) * 1000)
        return {
            "exit_code": result.returncode,
            "stdout": result.stdout if result.stdout is not None else "",
            "stderr": result.stderr if result.stderr is not None else "",
            "duration_ms": duration
        }
    except subprocess.TimeoutExpired as e:
        duration = int((time.time() - start_time) * 1000)
        stdout_str = e.stdout if isinstance(e.stdout, str) else (e.stdout.decode() if e.stdout else "")
        stderr_str = e.stderr if isinstance(e.stderr, str) else (e.stderr.decode() if e.stderr else "")
        return {
            "exit_code": -1,
            "stdout": stdout_str,
            "stderr": f"Error: Command timed out after {timeout} seconds. Raw stderr: {stderr_str}",
            "duration_ms": duration
        }
    except Exception as e:
        duration = int((time.time() - start_time) * 1000)
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": str(e),
            "duration_ms": duration
        }


def run_script(script_path: str, args: list | None = None, timeout: int = 120) -> dict:
    """
    Run a Python script using the project venv interpreter (preferred over host_exec for .py files).

    Args:
        script_path: Relative or absolute path to a .py file.
        args: Optional CLI arguments passed to the script.
        timeout: Maximum execution time in seconds.
    """
    from core.runtime_paths import search_roots, venv_python, venv_pip_command
    import os

    env = os.environ.copy()

    if not script_path:
        return {"exit_code": -1, "stdout": "", "stderr": "No script_path provided.", "duration_ms": 0}

    path = Path(script_path)
    script_cwd = None
    if not path.is_absolute():
        resolved: Path | None = None
        for root in search_roots():
            candidate = (root / path).resolve()
            if candidate.is_file():
                resolved = candidate
                script_cwd = root
                break
        path = resolved if resolved is not None else (search_roots()[0] / path).resolve()
    else:
        path = path.resolve()
    script_cwd = script_cwd or path.parent

    # #region agent log
    try:
        from core.debug_log import debug_log
        debug_log(
            "tools_legacy.py:run_script",
            "path resolution",
            {"input": script_path, "resolved": str(path), "exists": path.is_file()},
            "D",
        )
    except Exception:
        pass
    # #endregion

    if path.suffix.lower() != ".py":
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": f"run_script only supports .py files (got '{path.suffix}'). Use host_exec for PowerShell.",
            "duration_ms": 0,
        }
    if not path.is_file():
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": f"Script not found: {path}",
            "duration_ms": 0,
        }

    py = venv_python(near=path)
    cmd_args = [py, str(path)]
    if py.startswith("py "):
        cmd_args = py.split() + [str(path)]
    if args:
        cmd_args.extend(str(a) for a in args)

    start_time = time.time()
    try:
        result = subprocess.run(
            cmd_args,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(script_cwd),
            **_subprocess_run_kwargs(),
        )
        duration = int((time.time() - start_time) * 1000)
        out: dict = {
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "duration_ms": duration,
            "interpreter": py,
            "script": str(path),
            "cwd": str(script_cwd),
        }
        mod_match = re.search(r"No module named '([^']+)'", result.stderr or "")
        if mod_match:
            out["missing_module"] = mod_match.group(1)
            out["pip_install_command"] = venv_pip_command(
                f"install {mod_match.group(1)}", near=path
            )
        # #region agent log
        try:
            from core.debug_log import debug_log
            debug_log(
                "tools_legacy.py:run_script:result",
                "script execution",
                {
                    "exit_code": out["exit_code"],
                    "interpreter": py,
                    "cwd": str(script_cwd),
                    "missing_module": out.get("missing_module"),
                },
                "A",
                "run1",
            )
        except Exception:
            pass
        # #endregion
        return out
    except subprocess.TimeoutExpired as e:
        duration = int((time.time() - start_time) * 1000)
        stdout_str = e.stdout if isinstance(e.stdout, str) else (e.stdout.decode() if e.stdout else "")
        stderr_str = e.stderr if isinstance(e.stderr, str) else (e.stderr.decode() if e.stderr else "")
        return {
            "exit_code": -1,
            "stdout": stdout_str,
            "stderr": f"Error: Script timed out after {timeout} seconds. {stderr_str}",
            "duration_ms": duration,
            "interpreter": py,
            "script": str(path),
            "cwd": str(script_cwd),
        }
    except Exception as e:
        duration = int((time.time() - start_time) * 1000)
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": str(e),
            "duration_ms": duration,
            "interpreter": py,
            "script": str(path),
            "cwd": str(script_cwd),
        }


# ==========================================
# 3. Simple Filesystem Tools
# ==========================================

_PS1_EXTENSIONS = {".ps1", ".psm1", ".psd1"}


def _sanitize_powershell_content(content: str) -> tuple[str, int]:
    """
    Fix common LLM mistakes when embedding PowerShell in JSON write_file calls.

    1. Trailing line-continuation backtick after a closed single-quoted string (`'`$)
       — merges the next line and duplicates parameters like -ForegroundColor.
    2. Write-Host -Object '...`n...' using single quotes where backtick escapes are
       literal — convert to double quotes so `n / `t work as intended.
    """
    changes = 0
    out_lines: list[str] = []

    for line in content.splitlines(keepends=True):
        nl = ""
        body = line
        if line.endswith("\r\n"):
            body, nl = line[:-2], "\r\n"
        elif line.endswith("\n"):
            body, nl = line[:-1], "\n"

        if body.endswith("`") and re.search(r"'`$", body):
            body = body[:-1]
            changes += 1

        m = re.match(
            r"^(?P<prefix>\s*Write-Host\s+-ForegroundColor\s+\S+\s+-BackgroundColor\s+\S+\s+-Object\s+)'(?P<inner>.*)'$",
            body,
        )
        if m and ("`n" in m.group("inner") or "`t" in m.group("inner")):
            inner = m.group("inner").replace('"', '`"')
            body = f'{m.group("prefix")}"{inner}"'
            changes += 1

        out_lines.append(body + nl)

    return "".join(out_lines), changes


def read_file(path: str, line_start: int = 1, line_count: int = None) -> dict:
    """
    Reads the content of a local file. Supports chunked line reading for large documents.
    
    Args:
        path: Path to the file.
        line_start: 1-based index of the first line to read (default: 1).
        line_count: Number of lines to read from the file. If None, reads the entire file.
    """
    try:
        from core.path_catalog import resolve_read_target

        resolved, resolve_err = resolve_read_target(path)
        if resolve_err:
            return {"success": False, "error": resolve_err}
        file_path = resolved if resolved else Path(path).resolve()
        try:
            from core.session_visibility import path_visibility_error

            vis_err = path_visibility_error(file_path)
            if vis_err:
                return {"success": False, "error": vis_err}
        except Exception:
            pass
        if not file_path.exists():
            return {"success": False, "error": f"File '{path}' does not exist."}
        if not file_path.is_file():
            return {"success": False, "error": f"'{path}' is not a file."}
        if line_start < 1:
            return {"success": False, "error": "line_start must be >= 1."}
        if line_count is not None and line_count <= 0:
            return {"success": False, "error": "line_count must be > 0 when provided."}
        norm = str(file_path).replace("\\", "/").lower()
        if "/artifacts/" in norm and re.search(r"/(read_file|grep_file|analyze_pcapng)_\d", norm):
            # #region agent log
            try:
                from core.debug_log import debug_log
                debug_log(
                    "tools_legacy.py:read_file",
                    "blocked artifact spill read",
                    {"path": norm},
                    "B2",
                )
            except Exception:
                pass
            # #endregion
            return {
                "success": False,
                "error": (
                    f"'{path}' is a spilled tool-result artifact, not source data. "
                    "Use analyze_pcapng output, verbose_log_file, or find_and_grep on "
                    ".pulse/pcap_logs/verbose_*.txt instead."
                ),
            }
        if file_path.suffix.lower() in {".pcap", ".pcapng"}:
            # #region agent log
            try:
                from core.debug_log import debug_log
                debug_log(
                    "tools_legacy.py:read_file",
                    "blocked binary capture read",
                    {"path": str(file_path), "suffix": file_path.suffix.lower()},
                    "B1",
                )
            except Exception:
                pass
            # #endregion
            return {
                "success": False,
                "error": (
                    f"'{path}' appears to be a packet capture file. "
                    "Use analyze_pcapng(file_path=..., filter_expression='http', verbose=true) instead of read_file."
                ),
            }

        _MAX_UNBOUNDED_READ_BYTES = 2 * 1024 * 1024
        file_size = file_path.stat().st_size
        if line_count is None and file_size > _MAX_UNBOUNDED_READ_BYTES:
            return {
                "success": False,
                "error": (
                    f"File too large for unbounded read ({file_size} bytes). "
                    "Use line_count for chunked reading."
                ),
                "file_size_bytes": file_size,
            }

        content_lines: list[str] = []
        total_lines = 0
        start_idx = line_start - 1
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            if line_count is None:
                for idx, line in enumerate(f):
                    total_lines += 1
                    if idx >= start_idx:
                        content_lines.append(line)
            else:
                end_idx = start_idx + line_count
                for idx, line in enumerate(f):
                    total_lines += 1
                    if idx < start_idx:
                        continue
                    if idx >= end_idx:
                        continue
                    content_lines.append(line)

        lines_read = len(content_lines)
        has_more = (start_idx + lines_read) < total_lines
        next_line_start = (line_start + lines_read) if has_more else None
        content = "".join(content_lines)
        return {
            "success": True,
            "content": content,
            "line_start": line_start,
            "lines_read": lines_read,
            "total_lines": total_lines,
            "has_more": has_more,
            "next_line_start": next_line_start,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def grep_file(
    path: str,
    pattern: str,
    max_matches: int = 50,
    context_lines: int = 0,
    case_insensitive: bool = True,
) -> dict:
    """
    Search a file for matching lines and return compact, line-numbered matches.
    Useful for verbose logs/artifacts when read_file chunking is too broad.
    """
    try:
        from core.path_catalog import resolve_read_target

        resolved, resolve_err = resolve_read_target(path)
        if resolve_err:
            return {"success": False, "error": resolve_err}
        file_path = resolved if resolved else Path(path).resolve()
        try:
            from core.session_visibility import path_visibility_error

            vis_err = path_visibility_error(file_path)
            if vis_err:
                return {"success": False, "error": vis_err}
        except Exception:
            pass
        if not file_path.exists():
            return {"success": False, "error": f"File '{path}' does not exist."}
        if not file_path.is_file():
            return {"success": False, "error": f"'{path}' is not a file."}
        if not pattern:
            return {"success": False, "error": "pattern is required."}
        max_matches = max(1, min(int(max_matches), 500))
        context_lines = max(0, min(int(context_lines), 20))

        flags = re.IGNORECASE if case_insensitive else 0
        rx = re.compile(pattern, flags)
        lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
        out: list[dict] = []
        for idx, line in enumerate(lines, start=1):
            if not rx.search(line):
                continue
            start = max(1, idx - context_lines)
            end = min(len(lines), idx + context_lines)
            ctx = [
                {"line": ln, "text": lines[ln - 1]}
                for ln in range(start, end + 1)
            ]
            out.append({"line": idx, "text": line, "context": ctx})
            if len(out) >= max_matches:
                break

        return {
            "success": True,
            "path": str(file_path),
            "pattern": pattern,
            "matches": out,
            "truncated": len(out) >= max_matches,
            "match_count": len(out),
            "total_lines": len(lines),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def find_and_grep(
    pattern: str,
    path_glob: str = ".pulse/pcap_logs/verbose_*.txt",
    max_files: int = 5,
    max_matches_per_file: int = 20,
    context_lines: int = 0,
    case_insensitive: bool = True,
) -> dict:
    """
    Search multiple files matched by a filename glob for regex matches.
    Combines find_file ranking with grep_file per matched file.
    """
    try:
        if not pattern:
            return {"success": False, "error": "pattern is required."}
        max_files = max(1, min(int(max_files), 20))
        max_matches_per_file = max(1, min(int(max_matches_per_file), 200))

        res = find_file(path_glob)
        if not res.get("success"):
            return {
                "success": False,
                "error": res.get("error") or f"No files matched '{path_glob}'.",
                "files_searched": [],
            }

        ranked = res.get("matches") or []
        if res.get("recommended") and res["recommended"] in ranked:
            ranked = [res["recommended"]] + [m for m in ranked if m != res["recommended"]]
        elif res.get("recommended"):
            ranked = [res["recommended"]] + ranked

        file_results: list[dict] = []
        total_matches = 0
        for rel in ranked[:max_files]:
            hit = grep_file(
                rel,
                pattern,
                max_matches=max_matches_per_file,
                context_lines=context_lines,
                case_insensitive=case_insensitive,
            )
            if hit.get("success") and hit.get("match_count", 0) > 0:
                file_results.append({
                    "path": rel,
                    "match_count": hit.get("match_count", 0),
                    "matches": hit.get("matches", []),
                    "truncated": hit.get("truncated", False),
                })
                total_matches += hit.get("match_count", 0)

        return {
            "success": True,
            "pattern": pattern,
            "path_glob": path_glob,
            "files_searched": ranked[:max_files],
            "files_with_matches": len(file_results),
            "total_matches": total_matches,
            "results": file_results,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def write_file(path: str, content: str, session_id: str | None = None, deliverables: list[str] | None = None) -> dict:
    """
    Writes or overwrites a local file with the specified content.
    
    Args:
        path: Path to the file.
        content: The text content to write to the file.
        session_id: Active session (bare deliverable names route to session workspace).
        deliverables: Optional deliverable filenames from task intent.
    """
    try:
        from core.path_catalog import resolve_write_target
        from core.session_paths import load_active_session_id, ensure_session_layout

        sid = session_id or load_active_session_id()
        ensure_session_layout(sid)
        file_path = resolve_write_target(path, sid, deliverables)
        file_path.parent.mkdir(parents=True, exist_ok=True)

        original_len = len(content)
        sanitize_changes = 0
        if file_path.suffix.lower() in _PS1_EXTENSIONS:
            content, sanitize_changes = _sanitize_powershell_content(content)

        file_path.write_text(content, encoding="utf-8")
        msg = f"Successfully wrote to file '{file_path}'"
        if sanitize_changes:
            msg += f" (PowerShell sanitizer applied {sanitize_changes} fix(es))"
        return {"success": True, "message": msg, "sanitize_changes": sanitize_changes}
    except Exception as e:
        return {"success": False, "error": str(e)}


_ALLOWED_NOTE_FILES = frozenset({
    "plan.md", "status.md", "session_log.md",
})


def _append_note_path_allowed(path: str) -> bool:
    from core.session_paths import is_session_note_path
    normalized = path.replace("\\", "/")
    name = Path(normalized).name.lower()
    if is_session_note_path(normalized):
        return True
    return name in _ALLOWED_NOTE_FILES or bool(
        re.match(r"^(plan|status|session_log)_[\w]+\.md$", name)
    )


def append_note(path: str, line: str, session_id: str = "default") -> dict:
    """
    Append a timestamped note line to a workspace markdown file.
    Preserves all previous content — never overwrites.

    Args:
        path: Must be under workspace/ (e.g. workspace/plan.md).
        line: Single-line note to append.
        session_id: Session identifier included in the timestamp prefix.
    """
    try:
        from datetime import datetime
        from core.session_paths import normalize_note_path, ensure_session_layout

        ensure_session_layout(session_id)
        path = normalize_note_path(path, session_id)
        parts = [p.lower() for p in Path(path).parts]
        if "workspace" not in parts and "sessions" not in parts:
            return {
                "success": False,
                "error": f"append_note only allowed under workspace/ (got '{path}').",
            }

        file_path = Path(path)
        if not file_path.is_absolute():
            from core.runtime_paths import app_root
            file_path = (app_root() / path).resolve()
        if not _append_note_path_allowed(path):
            return {
                "success": False,
                "error": (
                    f"append_note restricted to {sorted(_ALLOWED_NOTE_FILES)} "
                    f"(got '{file_path.name}')."
                ),
            }

        file_path.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = f"[{ts} | session:{session_id}] {line.strip()}\n"

        if not file_path.exists():
            header = "# Session notes\n\n"
            file_path.write_text(header + entry, encoding="utf-8")
        else:
            with open(file_path, "a", encoding="utf-8") as f:
                f.write(entry)

        return {
            "success": True,
            "message": f"Appended note to '{path}'",
            "line": entry.strip(),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


# ==========================================
# 4. Wireshark Network Audit & Packet Analysis
# ==========================================

def find_tshark() -> str:
    """
    Locates the tshark.exe binary on a Windows host.
    """
    import shutil
    # 1. Environment Variable or PATH
    env_path = os.environ.get("TSHARK_PATH")
    if env_path and os.path.exists(env_path):
        return env_path
    
    path_check = shutil.which("tshark.exe") or shutil.which("tshark")
    if path_check:
        return path_check

    # 2. Known default Windows installations
    standard_paths = [
        r"C:\Program Files\Wireshark\tshark.exe",
        r"C:\Program Files (x86)\Wireshark\tshark.exe"
    ]
    for p in standard_paths:
        if os.path.exists(p):
            return p

    return ""


def find_file(name: str) -> dict:
    """
    Locate a file by name under the project tree.
    Returns all matches plus a recommended path (canonical location first).
    """
    from core.artifacts import find_file as _find
    return _find(name)


def list_network_interfaces() -> dict:
    """
    Lists all available local network interfaces with tshark.
    """
    tshark_path = find_tshark()
    if not tshark_path:
        return {
            "success": False,
            "error": "tshark.exe not found on system. Please install Wireshark or specify TSHARK_PATH."
        }

    try:
        # Run tshark -D to list interfaces
        result = subprocess.run(
            [tshark_path, "-D"],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode != 0:
            return {"success": False, "error": result.stderr or "Failed to list interfaces."}
        
        interfaces = []
        for line in result.stdout.strip().splitlines():
            if line:
                interfaces.append(line.strip())

        return {"success": True, "interfaces": interfaces}
    except Exception as e:
        return {"success": False, "error": str(e)}


def capture_packets(interface: str = "1", duration: int = 10, output_path: str = None) -> dict:
    """
    Captures live packets on a specified network interface for a set duration.
    
    Args:
        interface: Interface index or name (from list_network_interfaces).
        duration: Duration in seconds to capture packet traffic.
        output_path: Target path to save the .pcapng file. Default is "capture.pcapng" in temp or current directory.
    """
    tshark_path = find_tshark()
    if not tshark_path:
        return {
            "success": False,
            "error": "tshark.exe not found on system. Please install Wireshark or specify TSHARK_PATH."
        }

    if not output_path:
        output_path = str(Path("capture.pcapng").resolve())
    else:
        output_path = str(Path(output_path).resolve())

    try:
        # Command: tshark -i <interface> -a duration:<duration> -w <output_path>
        cmd = [tshark_path, "-i", str(interface), "-a", f"duration:{duration}", "-w", output_path]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=duration + 10
        )
        
        # Verify capture file was generated
        cap_file = Path(output_path)
        if cap_file.exists() and cap_file.stat().st_size > 0:
            return {
                "success": True,
                "message": f"Successfully captured packets for {duration} seconds.",
                "file_path": output_path,
                "file_size_bytes": cap_file.stat().st_size
            }
        else:
            return {
                "success": False,
                "error": f"Capture did not produce a valid file. Stderr: {result.stderr or 'No output.'}"
            }
    except Exception as e:
        return {"success": False, "error": str(e)}


def _tshark_field_table(
    tshark_path: str,
    pcap_path: Path,
    display_filter: str,
    fields: list[str],
    *,
    row_limit: int | None = None,
    timeout: int = 60,
) -> tuple[str, str]:
    """
    Run tshark -T fields. Do NOT pass -c: Wireshark applies -c to pre-filter
    packet reads, so early packets (MDNS/TLS) starve HTTP/login rows.
    """
    cmd = [tshark_path, "-r", str(pcap_path), "-Y", display_filter, "-T", "fields"]
    for field in fields:
        cmd.extend(["-e", field])
    cmd.extend(["-E", "header=y", "-E", "separator=\t", "-E", "quote=d"])
    res = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, **_subprocess_run_kwargs(),
    )
    if res.returncode != 0:
        return "", (res.stderr or res.stdout or "tshark fields failed").strip()
    out = res.stdout.strip()
    if row_limit is not None and out:
        lines = out.splitlines()
        if lines:
            out = "\n".join(lines[: row_limit + 1])
    return out, ""


def _decode_hex_http_body(hex_data: str, max_len: int = 400) -> str:
    """Best-effort decode of http.file_data hex into readable text for summaries."""
    if not hex_data or not re.fullmatch(r"[0-9a-fA-F]+", hex_data.replace(":", "")):
        return ""
    try:
        raw = bytes.fromhex(hex_data.replace(":", ""))
        text = raw.decode("utf-8", errors="replace")
        return text[:max_len]
    except (ValueError, TypeError):
        return ""


def analyze_pcapng(
    file_path: str,
    filter_expression: str = None,
    limit: int = 50,
    verbose: bool = False,
    show_bytes: bool = False
) -> dict:
    """
    Analyzes an existing pcapng/pcap file using tshark to yield protocol stats,
    network conversations, potential plaintext passwords, and custom filtered logs.
    Supports individual packet decodes with verbose and raw byte dumps.

    When verbose=True, the full -V output is written to a log file under
    workspace/pcap_logs/ and a targeted field-extraction summary is returned
    in the result dict for LLM context.  This avoids blowing up the context
    window with 100KB+ of raw protocol headers.
    """
    from core.artifacts import resolve_project_file

    tshark_path = find_tshark()
    if not tshark_path:
        return {
            "success": False,
            "error": "tshark.exe not found on system. Please install Wireshark or specify TSHARK_PATH."
        }

    pcap_path = resolve_project_file(file_path)
    if pcap_path is None:
        pcap_path = Path(file_path).resolve()
    else:
        pcap_path = pcap_path.resolve()

    # #region agent log
    try:
        from core.debug_log import debug_log
        debug_log(
            "tools_legacy.py:analyze_pcapng",
            "path resolution",
            {"input": file_path, "resolved": str(pcap_path), "exists": pcap_path.exists()},
            "D",
        )
    except Exception:
        pass
    # #endregion

    if not pcap_path.exists():
        return {"success": False, "error": f"File '{file_path}' does not exist."}

    analysis_report = {}

    # ── Context-budget constants ─────────────────────────────────────────
    # Maximum chars we allow the packet_summary section to occupy in
    # the JSON result that goes into the LLM context window.
    _MAX_SUMMARY_CHARS = 30_000
    # Per-packet size estimate for a verbose (-V) decode (bytes).
    _VERBOSE_PACKET_EST = 4_000

    try:
        # 1. Get Protocol Hierarchy Statistics
        cmd_phs = [tshark_path, "-r", str(pcap_path), "-z", "io,phs"]
        _sp = _subprocess_run_kwargs()
        res_phs = subprocess.run(cmd_phs, capture_output=True, text=True, timeout=30, **_sp)
        if res_phs.returncode == 0:
            phs = res_phs.stdout.strip()
            analysis_report["protocol_hierarchy"] = (
                phs[:4_000] + f"\n[... truncated from {len(phs)} chars]"
                if len(phs) > 4_000 else phs
            )

        # 2. Get IP Connections/Conversations
        cmd_conv = [tshark_path, "-r", str(pcap_path), "-z", "conv,ip"]
        res_conv = subprocess.run(cmd_conv, capture_output=True, text=True, timeout=30, **_sp)
        if res_conv.returncode == 0:
            analysis_report["ip_conversations"] = res_conv.stdout.strip()

        # 3. Check for plaintext credentials/body indicators
        cred_filter = (
            "http.authorization || "
            "http.request.uri contains \"login\" || "
            "http.request.uri contains \"loginData\" || "
            "http.file_data contains \"login\" || "
            "http.file_data contains \"password\" || "
            "http.file_data contains \"Password\" || "
            "http.file_data contains \"xmlObj\" || "
            "http contains \"xmlObj\" || "
            "ftp.request.command == \"USER\" || "
            "ftp.request.command == \"PASS\" || "
            "smtp.req.parameter"
        )
        cred_fields = [
            "frame.number",
            "ip.src",
            "ip.dst",
            "http.request.method",
            "http.request.uri",
            "http.authorization",
            "urlencoded-form.key",
            "urlencoded-form.value",
            "http.file_data",
        ]
        cred_out, cred_err = _tshark_field_table(
            tshark_path, pcap_path, cred_filter, cred_fields, timeout=45,
        )
        if cred_out:
            cred_lines = [cred_out]
            for line in cred_out.splitlines()[1:]:
                parts = line.split("\t")
                if len(parts) >= 9 and parts[8]:
                    preview = _decode_hex_http_body(parts[8])
                    if preview:
                        cred_lines.append(f"  decoded_body[{parts[0]}]: {preview}")
            analysis_report["potential_plaintext_credentials"] = "\n".join(cred_lines)[:_MAX_SUMMARY_CHARS]
        elif cred_err:
            analysis_report["credential_scan_error"] = cred_err[:500]

        # 3b. Compact index of all HTTP transactions (method + URI)
        http_index, _ = _tshark_field_table(
            tshark_path,
            pcap_path,
            "http",
            ["frame.number", "http.request.method", "http.request.uri"],
            timeout=45,
        )
        if http_index:
            analysis_report["http_index"] = http_index[:_MAX_SUMMARY_CHARS]

        # 3c. Form posts (login/password often live here, not in http.file_data)
        form_filter = (
            'http.request.method == "POST" and '
            '(http.request.uri contains "login" or http.request.uri contains "loginData")'
        )
        form_out, _ = _tshark_field_table(
            tshark_path,
            pcap_path,
            form_filter,
            [
                "frame.number",
                "http.request.uri",
                "urlencoded-form.key",
                "urlencoded-form.value",
            ],
            row_limit=50,
            timeout=45,
        )
        if form_out:
            analysis_report["http_forms"] = form_out[:_MAX_SUMMARY_CHARS]

        # 4. Filtered Packet List Output
        # ── Dynamic limit for verbose mode ──────────────────────────────
        effective_limit = limit
        if verbose:
            # Probe: grab 1 verbose packet to measure actual size
            probe_cmd = [tshark_path, "-r", str(pcap_path), "-V", "-c", "1"]
            if filter_expression:
                probe_cmd.extend(["-Y", filter_expression])
            probe_res = subprocess.run(
                probe_cmd, capture_output=True, text=True, timeout=15, **_sp
            )
            probe_size = len(probe_res.stdout) if probe_res.returncode == 0 else _VERBOSE_PACKET_EST
            probe_size = max(probe_size, 500)  # floor

            # How many packets can we fit within our context budget?
            dynamic_max = max(1, _MAX_SUMMARY_CHARS // probe_size)
            effective_limit = min(limit, dynamic_max)

        cmd_packets = [tshark_path, "-r", str(pcap_path)]
        if filter_expression:
            cmd_packets.extend(["-Y", filter_expression])

        if verbose:
            cmd_packets.append("-V")
        if show_bytes:
            cmd_packets.append("-x")

        cmd_packets.extend(["-c", str(effective_limit)])
        res_packets = subprocess.run(cmd_packets, capture_output=True, text=True, timeout=60, **_sp)

        if res_packets.returncode == 0:
            full_output = res_packets.stdout.strip()

            # 4a. Targeted field extraction (display filter BEFORE packet limit)
            display = filter_expression or "http"
            field_full, field_err = _tshark_field_table(
                tshark_path,
                pcap_path,
                display,
                [
                    "frame.number",
                    "ip.src",
                    "ip.dst",
                    "http.request.method",
                    "http.host",
                    "http.request.uri",
                    "http.content_type",
                    "http.authorization",
                    "http.set_cookie",
                    "http.cookie",
                    "http.file_data",
                    "urlencoded-form",
                ],
                row_limit=effective_limit,
                timeout=45,
            )
            if field_full:
                analysis_report["key_fields"] = field_full[:_MAX_SUMMARY_CHARS]
            elif field_err:
                analysis_report["key_fields_error"] = field_err[:500]

            # 4b. Persist large output for chunked read_file continuation
            if len(full_output) > _MAX_SUMMARY_CHARS or len(field_full) > _MAX_SUMMARY_CHARS:
                from core.runtime_paths import workspace_root
                log_dir = workspace_root() / ".pulse" / "pcap_logs"
                log_dir.mkdir(parents=True, exist_ok=True)
                ts = time.strftime("%Y%m%d_%H%M%S")
                log_path = log_dir / f"verbose_{ts}.txt"
                disk_payload = full_output if len(full_output) >= len(field_full) else field_full
                log_path.write_text(disk_payload, encoding="utf-8")
                analysis_report["verbose_log_file"] = str(log_path)
                analysis_report["verbose_log_lines"] = disk_payload.count("\n")
                analysis_report["verbose_log_bytes"] = len(disk_payload)
                analysis_report["packet_summary"] = (
                    full_output[:_MAX_SUMMARY_CHARS]
                    + f"\n\n[... TRUNCATED — full output ({len(full_output)} chars) "
                    f"saved to {log_path}. Use read_file(path=\"{log_path}\", line_start=1, line_count=100) to view in chunks.]"
                )
            else:
                analysis_report["packet_summary"] = full_output
        else:
            analysis_report["packet_summary_error"] = res_packets.stderr.strip()

        # 5. Extract evidence signal for mission completion gates
        combined_text = "\n".join([
            str(analysis_report.get("key_fields", "")),
            str(analysis_report.get("potential_plaintext_credentials", "")),
            str(analysis_report.get("http_forms", "")),
            str(analysis_report.get("http_index", "")),
            str(analysis_report.get("packet_summary", "")),
        ])
        if re.search(r"(login|password|xmlobj|logindata|username)", combined_text, re.I):
            analysis_report["extracted_secrets"] = True
            matches = sorted({m.group(0).lower() for m in re.finditer(r"(login|password|xmlobj)", combined_text, re.I)})
            analysis_report["extracted_keywords"] = matches
        else:
            analysis_report["extracted_secrets"] = False

        # #region agent log
        try:
            from core.debug_log import debug_log
            _creds = analysis_report.get("potential_plaintext_credentials", "")
            _kf = analysis_report.get("key_fields", "")
            _ps = analysis_report.get("packet_summary", "")
            debug_log(
                "tools_legacy.py:analyze_pcapng:return",
                "pcap analysis surfaced fields",
                {
                    "filter": filter_expression,
                    "verbose": verbose,
                    "has_creds": bool(_creds),
                    "creds_len": len(_creds),
                    "creds_head": _creds[:200],
                    "has_key_fields": bool(_kf),
                    "key_fields_len": len(_kf),
                    "key_fields_head": _kf[:300],
                    "packet_summary_len": len(_ps),
                    "has_summary_error": "packet_summary_error" in analysis_report,
                    "summary_error": analysis_report.get("packet_summary_error", "")[:300],
                    "has_login_kw": any(
                        k in (_kf + _ps + _creds + str(analysis_report.get("http_forms", ""))).lower()
                        for k in ("login", "password", "xmlobj", "username")
                    ),
                    "http_index_len": len(analysis_report.get("http_index", "")),
                    "http_forms_len": len(analysis_report.get("http_forms", "")),
                    "key_fields_error": analysis_report.get("key_fields_error", "")[:200],
                    "verbose_log_file": analysis_report.get("verbose_log_file"),
                },
                "BCD", "run1",
            )
        except Exception:
            pass
        # #endregion
        return {"success": True, "analysis": analysis_report}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _build_hashpro_argv(
    target_hash: str,
    mask: str,
    salt: str | None,
    min_len: int | None,
    known_prefix: str | None,
    known_suffix: str | None,
    wordlist: str | None,
    cpu_workers: int | None,
    no_gpu: bool,
) -> tuple[list[str], str, str]:
    """
    Build argv for non-interactive hash cracking.

    Prefers PATH ``hashpro`` (hashpro.bat → py -3.10 -u hash_pro7.py).
    Falls back to repo artifacts/scripts/hash_pro7.py via venv python -u.

    Returns (argv, launcher_label, cwd).
    """
    from core.runtime_paths import app_root, hash_pro7_script, hashpro_executable, venv_python

    hashpro = hashpro_executable()
    if hashpro:
        cmd: list[str] = [hashpro]
        launcher = "hashpro"
        cwd = str(app_root())
    else:
        script = hash_pro7_script()
        if not script.is_file():
            raise FileNotFoundError(f"hash_pro7.py not found (expected {script})")
        py = venv_python()
        if py.startswith("py "):
            cmd = py.split() + ["-u", str(script)]
        else:
            cmd = [py, "-u", str(script)]
        launcher = "hash_pro7.py"
        cwd = str(app_root())

    # hash_pro7 non-interactive mode: --target required (not -t / -s)
    cmd.extend(["--target", target_hash, "--mask", mask])
    if salt:
        cmd.extend(["--salt", salt])
    if min_len is not None:
        cmd.extend(["--min-len", str(min_len)])
    if known_prefix:
        cmd.extend(["--known-prefix", known_prefix])
    if known_suffix:
        cmd.extend(["--known-suffix", known_suffix])
    if wordlist:
        cmd.extend(["--wordlist", wordlist])
    if cpu_workers is not None:
        cmd.extend(["--cpu", str(cpu_workers)])
    if no_gpu:
        cmd.append("--no-gpu")

    return cmd, launcher, cwd


def crack_hash(
    target_hash: str,
    mask: str = "NNNNNNAA!",
    salt: str = None,
    min_len: int = None,
    known_prefix: str = None,
    known_suffix: str = None,
    wordlist: str = None,
    cpu_workers: int = None,
    no_gpu: bool = False,
    timeout: int = 1200,
    **kwargs: Any,
) -> dict:
    """
    Cracks a SHA-256 hash via PATH hashpro (preferred) or repo hash_pro7.py (venv, -u).
    Non-interactive when --target is passed (see hash_pro7 argparse).
    """
    if kwargs:
        ignored = ", ".join(sorted(kwargs))
        return {
            "success": False,
            "error": (
                f"Unsupported argument(s) for crack_hash: {ignored}. "
                "Use target_hash, mask, salt, min_len, known_prefix, known_suffix, "
                "wordlist, cpu_workers, no_gpu, timeout. "
                "Do not pass script_path or -t/-s (use --target/--salt via this tool)."
            ),
        }

    if not target_hash:
        return {"success": False, "error": "target_hash is required."}

    try:
        cmd, launcher, cwd = _build_hashpro_argv(
            target_hash, mask, salt, min_len, known_prefix, known_suffix,
            wordlist, cpu_workers, no_gpu,
        )
    except FileNotFoundError as e:
        return {"success": False, "error": str(e)}

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    env["TERM"] = "xterm-256color"

    # #region agent log
    try:
        from core.debug_log import debug_log
        debug_log(
            "tools_legacy.py:crack_hash",
            "launch",
            {"launcher": launcher, "cmd": cmd, "cwd": cwd},
            "H1",
        )
    except Exception:
        pass
    # #endregion

    try:
        import sys
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            cwd=cwd,
            **_subprocess_run_kwargs(),
        )

        stdout_chunks = []
        stderr_chunks = []
        start_time = time.time()

        while True:
            # Check timeout
            elapsed = time.time() - start_time
            if elapsed > timeout:
                process.kill()
                raise subprocess.TimeoutExpired(cmd, timeout)

            # Read a line from stdout
            line = process.stdout.readline()
            if not line:
                break
            # Print to host unbuffered console so user can monitor in real time
            sys.stdout.write(line)
            sys.stdout.flush()
            stdout_chunks.append(line)

        # After stdout ends, wait for the process to finish and capture stderr
        try:
            rem_stderr, _ = process.communicate(timeout=max(1.0, timeout - (time.time() - start_time)))
            stderr_chunks.append(rem_stderr)
        except subprocess.TimeoutExpired:
            process.kill()
            raise

        stdout_str = "".join(stdout_chunks)
        stderr_str = "".join(stderr_chunks)
        returncode = process.returncode if process.returncode is not None else -1

        # #region agent log
        try:
            from core.debug_log import debug_log
            debug_log(
                "tools_legacy.py:crack_hash",
                "finished",
                {
                    "returncode": returncode,
                    "stderr_head": stderr_str[:200],
                    "stdout_head": stdout_str[:200],
                },
                "H1",
            )
        except Exception:
            pass
        # #endregion

        # Check exit code or search for ENCONTRADO (which indicates success in the script)
        # Script format: ✓ ENCONTRADO → [password]
        found_match = re.search(r"(?:✓|\[\+\]|)\s*ENCONTRADO\s*(?:→|->)\s*(?:\[(.*?)\]|(.*?)(?:\n|$))", stdout_str)
        
        if found_match:
            # Group 1 if they used bracket format, Group 2 if raw string
            pwd = found_match.group(1) or found_match.group(2)
            pwd = pwd.strip()
            return {
                "success": True,
                "status": "cracked",
                "password": pwd,
                "stdout": stdout_str,
                "stderr": stderr_str
            }
        
        if "ModuleNotFoundError" in stderr_str or "No module named" in stderr_str:
            return {
                "success": False,
                "status": "dependency_error",
                "error": (
                    "hash_pro7.py missing Python dependencies in the project venv. "
                    "Install with: host_exec "
                    '& ".venv/Scripts/python.exe" -m pip install numpy rich'
                ),
                "stdout": stdout_str,
                "stderr": stderr_str,
            }

        if "No encontrado" in stdout_str or "No encontrado" in stderr_str or returncode != 0:
            err = "Password not found in the specified search space."
            if returncode != 0 and stderr_str.strip():
                err = f"{err} (exit {returncode}: {stderr_str.strip()[:300]})"
            return {
                "success": False,
                "status": "exhausted",
                "error": err,
                "stdout": stdout_str,
                "stderr": stderr_str
            }

        return {
            "success": False,
            "status": "unknown",
            "error": "Execution finished but did not explicitly output success or exhaustion.",
            "stdout": stdout_str,
            "stderr": stderr_str
        }

    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"Execution timed out after {timeout} seconds."}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ==========================================
# 5. Global Tool Directory Metadata (for LLM schema construction)
# ==========================================

TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "sequentialthinking",
            "description": "A stateful tool for dynamic and reflective problem-solving through thoughts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "thought": {"type": "string", "description": "Your current detailed thinking step."},
                    "nextThoughtNeeded": {"type": "boolean", "description": "Whether another sequential thought step is needed."},
                    "thoughtNumber": {"type": "integer", "description": "Current thought step index (1-based)."},
                    "totalThoughts": {"type": "integer", "description": "Current estimate of total thought steps needed."},
                    "isRevision": {"type": "boolean", "description": "True if this thought revises a previous thinking step."},
                    "revisesThought": {"type": "integer", "description": "The thought index that is being revised."}
                },
                "required": ["thought", "nextThoughtNeeded", "thoughtNumber", "totalThoughts"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "host_exec",
            "description": "Executes a shell command directly on the Windows host machine using PowerShell.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The PowerShell command line to execute."},
                    "timeout": {"type": "integer", "description": "Maximum execution time in seconds. Defaults to 120."}
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_script",
            "description": "Preferred way to run Python deliverables and tests. Uses the project .venv interpreter directly (not PowerShell).",
            "parameters": {
                "type": "object",
                "properties": {
                    "script_path": {"type": "string", "description": "Path to a .py file (e.g. watcher/watcher.py)."},
                    "args": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional CLI arguments for the script.",
                    },
                    "timeout": {"type": "integer", "description": "Maximum execution time in seconds. Defaults to 120."},
                },
                "required": ["script_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Reads the text contents of a local file from the system. Supports reading files by line chunking for large files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute or relative path to the file."},
                    "line_start": {"type": "integer", "description": "1-based index of the first line to read (default: 1)."},
                    "line_count": {"type": "integer", "description": "Optional number of lines to read. If omitted, reads the entire file."}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Writes text content directly to a local file, creating parent folders if needed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute or relative path to the file."},
                    "content": {"type": "string", "description": "The raw text content to write."}
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "append_note",
            "description": (
                "Append a single timestamped progress line to a workspace note file "
                "(workspace/plan.md, workspace/status.md, workspace/session_log.md). "
                "Preserves previous lines — use this for status updates, NOT write_file."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Workspace note path, e.g. workspace/plan.md",
                    },
                    "line": {
                        "type": "string",
                        "description": "One-line progress note to append.",
                    },
                    "session_id": {
                        "type": "string",
                        "description": "Session id for the timestamp prefix (default: default).",
                    },
                },
                "required": ["path", "line"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_file",
            "description": "Search the project tree for a file by name. Returns matches and a recommended path. Use before analyze_pcapng when the user names a PCAP or script file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Filename to search for (e.g. last_capture.pcapng)."},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep_file",
            "description": (
                "Search a local file for regex/text matches with optional context lines. "
                "Use for targeted retrieval from verbose logs/artifacts instead of broad read_file chunks."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to search."},
                    "pattern": {"type": "string", "description": "Regex pattern to match."},
                    "max_matches": {"type": "integer", "description": "Maximum matches to return (default 50)."},
                    "context_lines": {"type": "integer", "description": "Lines of context around each match (default 0)."},
                    "case_insensitive": {"type": "boolean", "description": "Enable case-insensitive matching."},
                },
                "required": ["path", "pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_and_grep",
            "description": (
                "Search file CONTENTS (regex) across files matched by path_glob. "
                "For locating files by NAME in workspace, use find_file then read_file — not this tool. "
                "Default path_glob targets PCAP verbose logs (.pulse/pcap_logs/verbose_*.txt) only."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regex pattern to match."},
                    "path_glob": {
                        "type": "string",
                        "description": "Filename glob (default .pulse/pcap_logs/verbose_*.txt).",
                    },
                    "max_files": {"type": "integer", "description": "Max files to search (default 5)."},
                    "max_matches_per_file": {
                        "type": "integer",
                        "description": "Max matches per file (default 20).",
                    },
                    "context_lines": {"type": "integer", "description": "Context lines per match (default 0)."},
                    "case_insensitive": {"type": "boolean", "description": "Case-insensitive matching."},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_network_interfaces",
            "description": "Lists all available local network interfaces with tshark.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "capture_packets",
            "description": "Captures live packets on a specified network interface for a set duration.",
            "parameters": {
                "type": "object",
                "properties": {
                    "interface": {"type": "string", "description": "Interface index or name (from list_network_interfaces). Defaults to '1'."},
                    "duration": {"type": "integer", "description": "Duration in seconds to capture packet traffic. Defaults to 10."},
                    "output_path": {"type": "string", "description": "Target path to save the .pcapng file. Defaults to 'capture.pcapng' in workspace."}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_pcapng",
            "description": (
                "Offline PCAP analysis via tshark. Workflow: find_file → broad filter (http, limit=30) "
                "→ narrow filter (http contains \"login\", frame.number == N) → verbose=true for field decode. "
                "Returns protocol stats, conversations, credentials scan, packet_summary; verbose dumps to .pulse/pcap_logs/."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "PCAP path (basename ok: last_capture.pcapng). Use find_file recommended path first.",
                    },
                    "filter_expression": {
                        "type": "string",
                        "description": (
                            "tshark display filter (NOT capture -f). Examples: "
                            "'http'; 'http contains \"login\"'; 'frame.number == 8'; "
                            "'http contains \"xml\" or http.content_type contains \"xml\"'."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max packets in summary pass (use 20-30 to index, then narrow filter). Default 50.",
                    },
                    "verbose": {
                        "type": "boolean",
                        "description": "true = tshark -V decode + key_fields extract; use after indexing with a narrow filter.",
                    },
                    "show_bytes": {
                        "type": "boolean",
                        "description": "true = hex/ASCII payload dump (tshark -x) for matched packets.",
                    },
                },
                "required": ["file_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "crack_hash",
            "description": (
                "Crack SHA-256 via PATH hashpro or hash_pro7.py. Plan mask+salt first. "
                "Salt is appended to each password candidate before hashing (required when user gave a salt). "
                "Mask: N=digit A=letter L=lower U=upper !=symbol. Do not use host_exec or -t/-s flags."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target_hash": {
                        "type": "string",
                        "description": "64-char SHA-256 hex digest to crack.",
                    },
                    "salt": {
                        "type": "string",
                        "description": (
                            "Salt string appended to password before SHA-256. "
                            "REQUIRED when user mentions salt (e.g. salt \"55077791\")."
                        ),
                    },
                    "mask": {
                        "type": "string",
                        "description": (
                            "Candidate format mask (default NNNNNNAA! = 6 digits + 2 letters + '!'). "
                            "Plan from password rules before running."
                        ),
                    },
                    "min_len": {
                        "type": "integer",
                        "description": "Minimum candidate length when password length is known.",
                    },
                    "known_prefix": {
                        "type": "string",
                        "description": "Fixed prefix prepended to every candidate (e.g. xmlObj).",
                    },
                    "known_suffix": {
                        "type": "string",
                        "description": "Fixed suffix appended to every candidate.",
                    },
                    "wordlist": {
                        "type": "string",
                        "description": "Optional wordlist file path to try before mask brute force.",
                    },
                    "cpu_workers": {"type": "integer", "description": "Parallel CPU workers."},
                    "no_gpu": {"type": "boolean", "description": "CPU-only (skip GPU/hashcat)."},
                    "timeout": {"type": "integer", "description": "Max seconds (default 180)."},
                },
                "required": ["target_hash"]
            }
        }
    }
]
