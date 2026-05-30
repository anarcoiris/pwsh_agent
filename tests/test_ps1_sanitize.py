"""Regression tests for PowerShell write_file sanitizer."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tools
from tools_legacy import _sanitize_powershell_content

# Exact broken content from audit_trail/2026-05-29.jsonl (creative helloworld write)
AUDIT_BROKEN = None
for line in Path("audit_trail/2026-05-29.jsonl").read_text(encoding="utf-8").splitlines():
    rec = json.loads(line)
    if rec.get("method") == "write_file" and "Write-Host" in rec.get("params", {}).get("content", ""):
        AUDIT_BROKEN = rec["params"]["content"]
        break

assert AUDIT_BROKEN, "audit sample not found"


def test_sanitize_removes_trailing_backticks():
    fixed, n = _sanitize_powershell_content(AUDIT_BROKEN)
    assert n >= 3, f"expected multiple fixes, got {n}"
    for i, ln in enumerate(fixed.splitlines(), 1):
        if "Write-Host" in ln:
            assert not ln.rstrip().endswith("`"), f"line {i} still ends with backtick: {ln!r}"


def test_sanitize_produces_runnable_script():
    fixed, _ = _sanitize_powershell_content(AUDIT_BROKEN)
    with tempfile.NamedTemporaryFile(suffix=".ps1", delete=False, mode="w", encoding="utf-8") as f:
        f.write(fixed)
        path = f.name
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-File", path],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert "ForegroundColor" not in (r.stderr or ""), r.stderr
        assert r.returncode == 0, r.stderr
        assert "Hello, World!" in r.stdout
    finally:
        Path(path).unlink(missing_ok=True)


def test_write_file_applies_sanitizer(tmp_path=None):
    out = Path(tempfile.mkdtemp()) / "test.ps1"
    result = tools.write_file(path=str(out), content=AUDIT_BROKEN)
    assert result["success"]
    assert result.get("sanitize_changes", 0) > 0
    written = out.read_text(encoding="utf-8")
    assert not any(ln.rstrip().endswith("`") for ln in written.splitlines() if "Write-Host" in ln)

print("All PS1 sanitizer tests passed.")
