import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tools


def test_run_script_absolute_path_resolves_cwd():
    p = Path(r"C:\Users\soyko\Documents\nosqlite\2343_28052026\optimizer\fetch_blockhash.py")
    if not p.is_file():
        return
    res = tools.run_script(str(p), timeout=2)
    assert "cannot access local variable 'root'" not in str(res.get("stderr", ""))
    if "cwd" in res:
        assert res.get("cwd") == str(p.parent)
