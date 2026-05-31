"""Tests for session-scoped workspace paths."""

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.session_paths import (
    ensure_session_layout,
    facts_file,
    facts_rel,
    generate_session_id,
    normalize_note_path,
    plan_note_rel,
    scratchpad_rel,
)


def test_ensure_session_layout_creates_files():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        sid = "20260531_120000"
        with patch("core.session_paths.app_root", return_value=root):
            paths = ensure_session_layout(sid)
            assert "plan" in paths
            assert (root / plan_note_rel(sid)).is_file()
            assert (root / paths["status"]).is_file()
            sp = root / scratchpad_rel(sid, "extract_secrets")
            sp.parent.mkdir(parents=True, exist_ok=True)
            sp.write_text("# scratch\n", encoding="utf-8")
            assert sp.is_file()


def test_normalize_legacy_plan_path():
    sid = "20260531_120000"
    rel = normalize_note_path("workspace/plan.md", sid)
    assert sid in rel
    assert rel.endswith(f"plan_{sid}.md")


def test_generate_session_id_format():
    sid = generate_session_id()
    assert len(sid) == 15
    assert sid[8] == "_"


def test_facts_paths():
    sid = "20260531_120000"
    assert facts_file(sid).name == "facts.json"
    assert facts_rel(sid).endswith("/facts.json")
