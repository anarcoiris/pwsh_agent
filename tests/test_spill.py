"""Tests for pointer-first spill helper."""

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.spill import maybe_spill_text


def test_maybe_spill_text_writes_file_when_large():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        with patch("core.session_paths.app_root", return_value=root):
            payload = "X" * 25000
            meta = maybe_spill_text("sid1", "host_exec", payload, threshold_chars=1000)
            assert meta is not None
            p = Path(meta["artifact_file"])
            assert p.exists()
            assert meta["artifact_bytes"] > 0
            assert "read_file(path=" in meta["artifact_note"]


def test_maybe_spill_text_noop_when_small():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        with patch("core.session_paths.app_root", return_value=root):
            assert maybe_spill_text("sid1", "host_exec", "small", threshold_chars=1000) is None
