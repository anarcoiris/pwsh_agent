"""Smoke test for run_script tool."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tools


def test_run_script_echo():
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as f:
        f.write('print("run_script_ok")\n')
        script = f.name

    try:
        result = tools.run_script(script_path=script, timeout=30)
        assert result.get("exit_code") == 0, result
        assert "run_script_ok" in result.get("stdout", "")
    finally:
        Path(script).unlink(missing_ok=True)


def test_run_script_rejects_ps1():
    result = tools.run_script(script_path="test.ps1")
    assert result.get("exit_code") == -1
    assert ".py" in result.get("stderr", "")


print("All run_script tests passed.")
