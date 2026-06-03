"""Session visibility fence tests."""

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.session_visibility import (
    filter_session_paths,
    is_path_visible,
    set_visibility_context,
    unlock_from_message,
)


def test_unlock_from_message():
    found = unlock_from_message("continue session 20260603_212948 please")
    assert "20260603_212948" in found


def test_other_session_hidden_when_fenced():
    set_visibility_context(
        active_session_id="20260603_222940",
        unlocked=set(),
        fence_enabled=True,
    )
    assert not is_path_visible("workspace/sessions/20260603_212948/plan_20260603_212948.md")
    assert is_path_visible("workspace/sessions/20260603_222940/plan_20260603_222940.md")


def test_unlocked_session_visible():
    set_visibility_context(
        active_session_id="20260603_222940",
        unlocked={"20260603_212948"},
        fence_enabled=True,
    )
    assert is_path_visible("workspace/sessions/20260603_212948/pwd.txt")


def test_filter_session_paths():
    set_visibility_context(active_session_id="20260603_111111", unlocked=set(), fence_enabled=True)
    paths = [
        "workspace/sessions/20260603_111111/pwd.txt",
        "workspace/sessions/20260603_222222/pwd.txt",
    ]
    filtered = filter_session_paths(paths)
    assert "workspace/sessions/20260603_111111/pwd.txt" in filtered
    assert "workspace/sessions/20260603_222222/pwd.txt" not in filtered
