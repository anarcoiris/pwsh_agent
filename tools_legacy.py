import os
import re
import sys
import time
import subprocess
from pathlib import Path
from typing import Any, Dict

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
        # Run command via PowerShell NoProfile/NonInteractive
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            text=True,
            timeout=timeout
        )
        duration = int((time.time() - start_time) * 1000)
        return {
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
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
    from core.runtime_paths import project_root, venv_python

    if not script_path:
        return {"exit_code": -1, "stdout": "", "stderr": "No script_path provided.", "duration_ms": 0}

    root = project_root()
    path = Path(script_path)
    if not path.is_absolute():
        path = root / path
    path = path.resolve()

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

    py = venv_python()
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
            cwd=str(root),
        )
        duration = int((time.time() - start_time) * 1000)
        out: dict = {
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "duration_ms": duration,
            "interpreter": py,
            "script": str(path),
        }
        mod_match = re.search(r"No module named '([^']+)'", result.stderr or "")
        if mod_match:
            out["missing_module"] = mod_match.group(1)
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
        }
    except Exception as e:
        duration = int((time.time() - start_time) * 1000)
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": str(e),
            "duration_ms": duration,
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
        file_path = Path(path).resolve()
        if not file_path.exists():
            return {"success": False, "error": f"File '{path}' does not exist."}
        if not file_path.is_file():
            return {"success": False, "error": f"'{path}' is not a file."}
        
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
            
        total_lines = len(lines)
        start_idx = max(0, line_start - 1)
        
        if line_count is not None:
            end_idx = min(total_lines, start_idx + line_count)
            content_lines = lines[start_idx:end_idx]
        else:
            content_lines = lines[start_idx:]
            
        content = "".join(content_lines)
        return {
            "success": True,
            "content": content,
            "line_start": line_start,
            "lines_read": len(content_lines),
            "total_lines": total_lines,
            "has_more": (start_idx + len(content_lines)) < total_lines
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def write_file(path: str, content: str) -> dict:
    """
    Writes or overwrites a local file with the specified content.
    
    Args:
        path: Path to the file.
        content: The text content to write to the file.
    """
    try:
        file_path = Path(path).resolve()
        # Create directories if they do not exist
        file_path.parent.mkdir(parents=True, exist_ok=True)

        original_len = len(content)
        sanitize_changes = 0
        if file_path.suffix.lower() in _PS1_EXTENSIONS:
            content, sanitize_changes = _sanitize_powershell_content(content)

        file_path.write_text(content, encoding="utf-8")
        msg = f"Successfully wrote to file '{path}'"
        if sanitize_changes:
            msg += f" (PowerShell sanitizer applied {sanitize_changes} fix(es))"
        return {"success": True, "message": msg, "sanitize_changes": sanitize_changes}
    except Exception as e:
        return {"success": False, "error": str(e)}


_ALLOWED_NOTE_FILES = frozenset({
    "plan.md", "status.md", "session_log.md",
})


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

        parts = [p.lower() for p in Path(path).parts]
        if "workspace" not in parts:
            return {
                "success": False,
                "error": f"append_note only allowed under workspace/ (got '{path}').",
            }

        file_path = Path(path).resolve()
        if file_path.name.lower() not in _ALLOWED_NOTE_FILES:
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
        res_phs = subprocess.run(cmd_phs, capture_output=True, text=True, timeout=30)
        if res_phs.returncode == 0:
            analysis_report["protocol_hierarchy"] = res_phs.stdout.strip()

        # 2. Get IP Connections/Conversations
        cmd_conv = [tshark_path, "-r", str(pcap_path), "-z", "conv,ip"]
        res_conv = subprocess.run(cmd_conv, capture_output=True, text=True, timeout=30)
        if res_conv.returncode == 0:
            analysis_report["ip_conversations"] = res_conv.stdout.strip()

        # 3. Check for plaintext credentials / unencrypted protocols (HTTP/FTP/SMTP/TELNET)
        cred_filter = "http.authorization || ftp.request.command == \"USER\" || ftp.request.command == \"PASS\" || smtp.req.parameter"
        cmd_creds = [tshark_path, "-r", str(pcap_path), "-Y", cred_filter, "-T", "fields", "-e", "frame.number", "-e", "ip.src", "-e", "ip.dst", "-e", "col.Protocol", "-e", "col.Info"]
        res_creds = subprocess.run(cmd_creds, capture_output=True, text=True, timeout=30)
        if res_creds.returncode == 0 and res_creds.stdout.strip():
            analysis_report["potential_plaintext_credentials"] = res_creds.stdout.strip()

        # 4. Filtered Packet List Output
        # ── Dynamic limit for verbose mode ──────────────────────────────
        effective_limit = limit
        if verbose:
            # Probe: grab 1 verbose packet to measure actual size
            probe_cmd = [tshark_path, "-r", str(pcap_path), "-V", "-c", "1"]
            if filter_expression:
                probe_cmd.extend(["-Y", filter_expression])
            probe_res = subprocess.run(
                probe_cmd, capture_output=True, text=True, timeout=15
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
        res_packets = subprocess.run(cmd_packets, capture_output=True, text=True, timeout=60)

        if res_packets.returncode == 0:
            full_output = res_packets.stdout.strip()

            if verbose and len(full_output) > _MAX_SUMMARY_CHARS:
                # ── Dump full output to log file ────────────────────────
                log_dir = Path(__file__).resolve().parent / "workspace" / "pcap_logs"
                log_dir.mkdir(parents=True, exist_ok=True)
                ts = time.strftime("%Y%m%d_%H%M%S")
                log_name = f"verbose_{ts}.txt"
                log_path = log_dir / log_name
                log_path.write_text(full_output, encoding="utf-8")
                analysis_report["verbose_log_file"] = str(log_path)
                analysis_report["verbose_log_lines"] = full_output.count("\n")
                analysis_report["verbose_log_bytes"] = len(full_output)

                # ── Targeted field extraction for context ───────────────
                # Instead of the full -V dump, run a second pass extracting
                # only the fields the user typically cares about.
                field_cmd = [
                    tshark_path, "-r", str(pcap_path),
                    "-T", "fields",
                    "-e", "frame.number",
                    "-e", "ip.src", "-e", "ip.dst",
                    "-e", "col.Protocol",
                    "-e", "http.request.method",
                    "-e", "http.host",
                    "-e", "http.request.uri",
                    "-e", "http.content_type",
                    "-e", "http.authorization",
                    "-e", "http.set_cookie",
                    "-e", "http.cookie",
                    "-e", "http.file_data",
                    "-e", "xml",
                    "-e", "data.text",
                    "-e", "text",
                    "-E", "header=y",
                    "-E", "separator=\t",
                    "-E", "quote=d",
                    "-c", str(effective_limit),
                ]
                if filter_expression:
                    field_cmd.extend(["-Y", filter_expression])
                field_res = subprocess.run(
                    field_cmd, capture_output=True, text=True, timeout=30,
                )
                if field_res.returncode == 0 and field_res.stdout.strip():
                    analysis_report["key_fields"] = field_res.stdout.strip()[:_MAX_SUMMARY_CHARS]

                # Keep a truncated head of the verbose output as context preview
                analysis_report["packet_summary"] = (
                    full_output[:_MAX_SUMMARY_CHARS]
                    + f"\n\n[... TRUNCATED — full output ({len(full_output)} chars) "
                    f"saved to {log_path}. Use read_file to view in chunks.]"
                )
            else:
                analysis_report["packet_summary"] = full_output
        else:
            analysis_report["packet_summary_error"] = res_packets.stderr.strip()

        return {"success": True, "analysis": analysis_report}
    except Exception as e:
        return {"success": False, "error": str(e)}


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
    timeout: int = 180
) -> dict:
    """
    Cracks a SHA-256 target hash using the hybrid CPU+GPU hash_pro7.py script.
    Executes non-interactively using 'py -3.10 hash_pro7.py' to avoid encoding/compatibility issues.

    Uses the confirmed-working script at:
        C:\\Users\\soyko\\Documents\\MCP_Pentesting\\tools\\hash_crack\\hash_pro7.py
    CWD is set to that directory so hashcat.exe and its OpenCL/kernel resources resolve correctly.
    """
    if not target_hash:
        return {"success": False, "error": "target_hash is required."}

    # Use our workspace duplicate of hash_pro7.py which contains the custom CP1252/OEM decoding fixes.
    # We still keep the current working directory (cwd) pointing to MCP_Pentesting/tools/hash_crack
    # so hashcat.exe, OpenCL kernels, rules, and potfiles resolve successfully.
    HASH_CRACK_DIR = Path(r"C:\Users\soyko\Documents\MCP_Pentesting\tools\hash_crack")
    WORKSPACE_DIR = Path(__file__).resolve().parent
    script_path = str(WORKSPACE_DIR / "hash_pro7.py")
    if not os.path.exists(script_path):
        return {"success": False, "error": f"Workspace hash_pro7.py was not found at {script_path}."}

    # Pass the global Python UTF-8 override flag (-X utf8) to py -3.10 to prevent standard output unicode errors
    cmd = ["py", "-3.10", "-X", "utf8", script_path, "--target", target_hash, "--mask", mask]
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

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    # Prevent rich from falling back to the legacy Win32 console renderer
    # (LegacyWindowsTerm), which can't encode Unicode chars like → in cp1252.
    # Setting TERM forces rich onto the ANSI/VT100 code path.
    env["TERM"] = "xterm-256color"

    try:
        # Run process non-interactively with cwd set to hash_crack dir so
        # hashcat.exe can find its OpenCL kernels, potfiles, and other resources.
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            cwd=str(HASH_CRACK_DIR),
            timeout=timeout
        )

        stdout_str = result.stdout or ""
        stderr_str = result.stderr or ""

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
        
        if "No encontrado" in stdout_str or "No encontrado" in stderr_str or result.returncode != 0:
            return {
                "success": False,
                "status": "exhausted",
                "error": "Password not found in the specified search space.",
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
            "description": "Analyzes an existing pcapng/pcap file using tshark to yield protocol stats, network conversations, potential plaintext passwords, and custom filtered logs. Supports verbose packet decodes and hex byte dumps.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Absolute or relative path to the pcapng file."},
                    "filter_expression": {"type": "string", "description": "Optional Wireshark/tshark display filter expression (e.g. 'frame.number == 8' or 'http')."},
                    "limit": {"type": "integer", "description": "Maximum number of packet summary lines to return. Defaults to 50."},
                    "verbose": {"type": "boolean", "description": "If true, returns a full verbose protocol decode for matching packets (tshark -V). Defaults to false."},
                    "show_bytes": {"type": "boolean", "description": "If true, returns a hex/ASCII dump of raw packet payload bytes (tshark -x). Defaults to false."}
                },
                "required": ["file_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "crack_hash",
            "description": "Cracks a SHA-256 target hash using the high-performance CPU+GPU hash_pro7.py tool via a robust non-interactive runner.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_hash": {"type": "string", "description": "The target SHA-256 hash to crack (hex-encoded, 64 characters)."},
                    "mask": {"type": "string", "description": "Character set mask matching password format (e.g. 'NNNNNNAA!', default: 'NNNNNNAA!')."},
                    "salt": {"type": "string", "description": "Optional salt concatenated to password candidate prior to hashing."},
                    "min_len": {"type": "integer", "description": "Minimum incremental length to start cracking from."},
                    "known_prefix": {"type": "string", "description": "Optional known string prefix to prepend to candidate passwords."},
                    "known_suffix": {"type": "string", "description": "Optional known string suffix to append to candidate passwords."},
                    "wordlist": {"type": "string", "description": "Optional file path to a wordlist dictionary file to search first."},
                    "cpu_workers": {"type": "integer", "description": "Optional number of parallel CPU worker threads."},
                    "no_gpu": {"type": "boolean", "description": "If true, completely bypasses GPU acceleration (CuPy/hashcat) and runs CPU-only."},
                    "timeout": {"type": "integer", "description": "Maximum tool execution timeout in seconds. Defaults to 180."}
                },
                "required": ["target_hash"]
            }
        }
    }
]
