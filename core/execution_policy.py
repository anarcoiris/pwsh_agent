"""
core/execution_policy.py — Redirect or rewrite tool calls before dispatch.

Handles host_exec → run_script redirects and venv-aware pip/python commands.
"""

from __future__ import annotations

import re
from typing import Any

from pathlib import Path

from core.runtime_paths import venv_pip_command, venv_python

_PIP_NEAR: Path | None = None


def set_pip_near(path: str | Path | None) -> None:
    """Set script path context for the next pip host_exec normalization."""
    global _PIP_NEAR
    _PIP_NEAR = Path(path) if path else None


class ExecutionPolicy:
    """Apply execution routing rules before tool dispatch."""

    _PS_FILE_PY = re.compile(
        r"powershell(?:\.exe)?\s+.*-File\s+['\"]?([^\s'\"]+\.py)['\"]?",
        re.I,
    )
    _PYTHON_SCRIPT = re.compile(
        r"^\s*(?:python|py(?:\s+-3\.10)?)\s+([^\s'\"]+\.py)(?:\s+(.*))?$",
        re.I,
    )
    _PIP_INSTALL = re.compile(
        r"^\s*(?:python|py(?:\s+-3\.10)?)\s+-m\s+pip\s+(install\s+.+)$",
        re.I,
    )
    _REL_VENV_PIP = re.compile(
        r'(?:&\s+)?["\']?\.venv[\\/]Scripts[\\/]python\.exe["\']?\s+-m\s+pip\s+(install\s+.+)$',
        re.I,
    )

    @classmethod
    def apply(
        cls,
        tool_name: str,
        args: dict[str, Any],
    ) -> tuple[str, dict[str, Any], str | None]:
        """
        Returns (tool_name, args, redirect_note).
        redirect_note is informational text appended to tool result when redirected.
        """
        if tool_name != "host_exec":
            return tool_name, args, None

        command = str(args.get("command", "")).strip()
        if not command:
            return tool_name, args, None

        m = cls._PS_FILE_PY.search(command)
        if m:
            script = m.group(1).replace("\\", "/")
            return "run_script", {"script_path": script, "timeout": args.get("timeout", 120)}, (
                f"Redirected host_exec PowerShell -File on .py → run_script({script})"
            )

        m = cls._PYTHON_SCRIPT.match(command)
        if m:
            script = m.group(1).replace("\\", "/")
            extra = (m.group(2) or "").strip()
            run_args: dict[str, Any] = {
                "script_path": script,
                "timeout": args.get("timeout", 120),
            }
            if extra:
                run_args["args"] = extra.split()
            return "run_script", run_args, (
                f"Redirected host_exec python invocation → run_script({script})"
            )

        m = cls._REL_VENV_PIP.search(command)
        if m:
            near = cls._script_near_from_context(args)
            pip_cmd = venv_pip_command(m.group(1).strip(), near=near)
            new_args = dict(args)
            new_args["command"] = pip_cmd
            return tool_name, new_args, (
                f"Normalized relative .venv pip to absolute interpreter: {venv_python(near=near)}"
            )

        m = cls._PIP_INSTALL.match(command)
        if m:
            near = cls._script_near_from_context(args)
            pip_cmd = venv_pip_command(m.group(1).strip(), near=near)
            new_args = dict(args)
            new_args["command"] = pip_cmd
            return tool_name, new_args, (
                f"Normalized pip command to use venv interpreter: {venv_python(near=near)}"
            )

        if re.search(r"^\s*python\s+", command, re.I) and "-m pip" not in command.lower():
            py = venv_python()
            if not py.startswith("py "):
                new_cmd = re.sub(r"^\s*python\s+", f'& "{py}" ', command, count=1, flags=re.I)
            else:
                new_cmd = re.sub(r"^\s*python\s+", f"{py} ", command, count=1, flags=re.I)
            new_args = dict(args)
            new_args["command"] = new_cmd
            return tool_name, new_args, f"Normalized python to venv: {py}"

        return tool_name, args, None

    @staticmethod
    def _script_near_from_context(args: dict[str, Any]) -> Path | None:
        """Best-effort script path hint for venv resolution during pip installs."""
        global _PIP_NEAR
        if _PIP_NEAR is not None:
            return _PIP_NEAR
        for key in ("script_path", "script", "path"):
            val = args.get(key)
            if val:
                return Path(str(val))
        return None
