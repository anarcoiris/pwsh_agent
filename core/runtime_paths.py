"""Resolve project root and venv-aware Python executables."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

_APP_ROOT = Path(__file__).resolve().parent.parent
_ARTIFACTS_DIR = _APP_ROOT / "artifacts"
_ARTIFACTS_SCRIPTS = _ARTIFACTS_DIR / "scripts"
_ARTIFACTS_CAPTURES = _ARTIFACTS_DIR / "captures"


def app_root() -> Path:
    """The directory where the agent's code, configs, and playbooks live."""
    return _APP_ROOT


def workspace_root() -> Path:
    """The directory where the console was invoked from, used for sandboxed deliverables."""
    return Path.cwd()


def artifacts_dir() -> Path:
    return _ARTIFACTS_DIR


def artifacts_scripts_dir() -> Path:
    return _ARTIFACTS_SCRIPTS


def artifacts_captures_dir() -> Path:
    return _ARTIFACTS_CAPTURES


def search_roots() -> tuple[Path, ...]:
    """Distinct roots to search for repo artifacts (workspace first, then app install)."""
    seen: set[Path] = set()
    ordered: list[Path] = []
    for root in (workspace_root(), app_root()):
        resolved = root.resolve()
        if resolved not in seen:
            seen.add(resolved)
            ordered.append(resolved)
    return tuple(ordered)


def bootstrap_sys_path(extra_root: Path | None = None) -> Path:
    """Ensure repository root is importable; return that root."""
    root = (extra_root or app_root()).resolve()
    root_s = str(root)
    if root_s not in sys.path:
        sys.path.insert(0, root_s)
    return root


def repo_root_from_script(script_file: str | Path) -> Path:
    """Resolve app root from a file under artifacts/scripts/."""
    path = Path(script_file).resolve()
    if path.parent.name == "scripts" and path.parent.parent.name == "artifacts":
        return path.parent.parent.parent
    return app_root()


def venv_python(near: Path | str | None = None) -> str:
    """
    Resolve interpreter for run_script / pip.

    - Scripts under app_root: walk up from script dir for a local .venv.
    - External scripts (outside repo): use app_root .venv so deps install in the
      agent runtime, not unrelated parent directories like ~/.venv.
    """
    if near is not None:
        start = Path(near).resolve()
        if start.is_file():
            start = start.parent
        try:
            start.relative_to(_APP_ROOT)
            in_repo = True
        except ValueError:
            in_repo = False

        if in_repo:
            current = start
            for _ in range(8):
                venv_exe = current / ".venv" / "Scripts" / "python.exe"
                if venv_exe.is_file():
                    return str(venv_exe)
                if current.parent == current:
                    break
                current = current.parent

    venv_exe = _APP_ROOT / ".venv" / "Scripts" / "python.exe"
    if venv_exe.is_file():
        return str(venv_exe)
    if shutil.which("py"):
        return "py -3.10"
    py = shutil.which("python")
    return py or "python"


def hash_pro7_script() -> Path:
    """Canonical path to the hybrid hash cracker script (repo copy)."""
    path = _ARTIFACTS_SCRIPTS / "hash_pro7.py"
    if path.is_file():
        return path.resolve()
    from core.artifacts import resolve_project_file

    found = resolve_project_file("hash_pro7.py")
    return found.resolve() if found else path


def hashpro_executable() -> str | None:
    """PATH launcher (hashpro.bat): py -3.10 -u hash_pro7.py with hashcat cwd."""
    for name in ("hashpro", "hashpro.bat"):
        found = shutil.which(name)
        if found:
            return found
    return None


def venv_pip_command(install_args: str, near: Path | str | None = None) -> str:
    """Build a host_exec-safe pip install command using the resolved venv interpreter."""
    py = venv_python(near=near)
    if py.startswith("py "):
        return f"{py} -m pip {install_args}"
    return f'& "{py}" -m pip {install_args}'
