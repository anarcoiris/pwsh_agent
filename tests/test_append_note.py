"""Tests for append_note tool."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tools


def test_append_preserves_lines():
    with tempfile.TemporaryDirectory() as tmp:
        note_path = Path(tmp) / "workspace" / "plan.md"
        note_path.parent.mkdir(parents=True)
        note_path.write_text("# Session notes\n\n[old line]\n", encoding="utf-8")

        r1 = tools.append_note(path=str(note_path), line="First new note", session_id="test")
        r2 = tools.append_note(path=str(note_path), line="Second new note", session_id="test")
        assert r1["success"] and r2["success"]

        text = note_path.read_text(encoding="utf-8")
        assert "[old line]" in text
        assert "First new note" in text
        assert "Second new note" in text
        assert text.count("[") >= 3


def test_rejects_outside_workspace():
    r = tools.append_note(path="watcher/plan.md", line="nope")
    assert not r["success"]
    assert "workspace" in r["error"].lower()


print("All append_note tests passed.")
