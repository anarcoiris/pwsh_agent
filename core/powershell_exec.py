"""Launch Windows PowerShell 5.1 for tool subprocesses.

Pulse is often started from PowerShell 7 or a PS7-hosted terminal. Spawning
``powershell`` from PATH frequently resolves to pwsh, which breaks
``Microsoft.PowerShell.Security`` autoload (ConvertTo-SecureString) and can
trigger TypeData conflicts when modules from PS7 and 5.1 mix — see
https://github.com/PowerShell/PowerShell/issues/18530
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_WIN_PS_51 = (
    Path(os.environ.get("WINDIR", r"C:\Windows"))
    / "System32"
    / "WindowsPowerShell"
    / "v1.0"
    / "powershell.exe"
)

_WIN_SUBPROCESS_FLAGS = (
    subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
)


def powershell_executable() -> str:
    """Return the PowerShell executable used for recon/host_exec subprocesses."""
    override = (os.environ.get("PULSE_POWERSHELL_EXE") or "").strip()
    if override:
        return override
    if sys.platform == "win32" and _WIN_PS_51.is_file():
        return str(_WIN_PS_51)
    return "powershell"


def subprocess_run_kwargs() -> dict:
    kwargs: dict = {"stdin": subprocess.DEVNULL}
    if _WIN_SUBPROCESS_FLAGS:
        kwargs["creationflags"] = _WIN_SUBPROCESS_FLAGS
    return kwargs


def run_powershell(command: str, *, timeout: int = 30) -> dict:
    """Run a PowerShell -Command script and return structured output."""
    exe = powershell_executable()
    try:
        result = subprocess.run(
            [exe, "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
            **subprocess_run_kwargs(),
        )
        return {
            "success": result.returncode == 0,
            "stdout": (result.stdout or "").strip(),
            "stderr": (result.stderr or "").strip(),
            "exit_code": result.returncode,
            "powershell_exe": exe,
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": f"Command timed out after {timeout}s",
            "powershell_exe": exe,
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "powershell_exe": exe,
        }
