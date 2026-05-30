"""Resolve project root and venv-aware Python executables."""

from __future__ import annotations

import shutil
from pathlib import Path

_APP_ROOT = Path(__file__).resolve().parent.parent

def app_root() -> Path:
    """The directory where the agent's code, configs, and playbooks live."""
    return _APP_ROOT

def workspace_root() -> Path:
    """The directory where the console was invoked from, used for sandboxed deliverables."""
    return Path.cwd()


def venv_python() -> str:
    """Prefer .venv/Scripts/python.exe, then py -3.10, then python on PATH."""
    venv_exe = _APP_ROOT / ".venv" / "Scripts" / "python.exe"
    if venv_exe.is_file():
        return str(venv_exe)
    if shutil.which("py"):
        return "py -3.10"
    py = shutil.which("python")
    return py or "python"


def venv_pip_command(install_args: str) -> str:
    """Build a host_exec-safe pip install command using the venv interpreter."""
    py = venv_python()
    if py.startswith("py "):
        return f"{py} -m pip {install_args}"
    return f'& "{py}" -m pip {install_args}'
