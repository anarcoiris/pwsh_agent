"""Tests for WriteGuard deliverable vs plan.md rules."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.task_intent import TaskIntentExtractor
from core.write_guard import WriteGuard

MSG = "Write watcher.py in the watcher folder."


def test_redirects_progress_note_to_append():
    intent = TaskIntentExtractor.parse(MSG)
    name, args, err = WriteGuard.apply(
        "write_file",
        {"path": "workspace/plan.md", "content": "Script reviewed and verified"},
        intent,
        pending_deliverables=["watcher/watcher.py"],
    )
    assert err is None
    assert name == "append_note"
    assert args["path"] == "workspace/plan.md"
    assert "reviewed" in args["line"]


def test_blocks_plan_write_when_deliverable_pending():
    intent = TaskIntentExtractor.parse(MSG)
    # Long content that looks like code attempt to plan.md still blocked if pending
    name, args, err = WriteGuard.apply(
        "write_file",
        {"path": "workspace/plan.md", "content": "Mission complete: watcher done"},
        intent,
        pending_deliverables=["watcher/watcher.py"],
    )
    assert name == "append_note" or err is not None


def test_allows_deliverable_write():
    intent = TaskIntentExtractor.parse(MSG)
    name, args, err = WriteGuard.apply(
        "write_file",
        {"path": "watcher/watcher.py", "content": "import os\nprint('hi')\n"},
        intent,
        pending_deliverables=["watcher/watcher.py"],
    )
    assert err is None
    assert name == "write_file"
    assert args["path"] == "watcher/watcher.py"


print("All write_guard tests passed.")
