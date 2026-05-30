"""Tests for ExecutionPolicy redirects and normalizations."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.execution_policy import ExecutionPolicy


def test_redirects_powershell_file_py_to_run_script():
    name, args, note = ExecutionPolicy.apply(
        "host_exec",
        {"command": "powershell -ExecutionPolicy Bypass -File watcher/watcher.py"},
    )
    assert name == "run_script"
    assert args["script_path"] == "watcher/watcher.py"
    assert note is not None


def test_redirects_python_script_to_run_script():
    name, args, note = ExecutionPolicy.apply(
        "host_exec",
        {"command": "python watcher/watcher.py"},
    )
    assert name == "run_script"
    assert args["script_path"] == "watcher/watcher.py"


def test_normalizes_pip_install():
    name, args, note = ExecutionPolicy.apply(
        "host_exec",
        {"command": "python -m pip install watchdog"},
    )
    assert name == "host_exec"
    assert "pip install watchdog" in args["command"]
    assert note is not None


def test_passthrough_unrelated_host_exec():
    name, args, note = ExecutionPolicy.apply(
        "host_exec",
        {"command": "Get-Date"},
    )
    assert name == "host_exec"
    assert args["command"] == "Get-Date"
    assert note is None


print("All execution_policy tests passed.")
