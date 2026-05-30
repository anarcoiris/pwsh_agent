"""Tests for TaskIntentExtractor."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.task_intent import TaskIntentExtractor

MSG = (
    "Can you write a watcher.py script that maps this folder and reports file changes? "
    "Save it in the watcher folder. Do not run network tasks."
)


def test_extracts_watcher_deliverable():
    intent = TaskIntentExtractor.parse(MSG)
    assert "watcher/watcher.py" in intent.deliverables
    assert "watcher.py" not in intent.deliverables
    assert intent.is_dev_task
    assert intent.forbid_network


def test_pending_deliverables():
    intent = TaskIntentExtractor.parse(MSG)
    root = Path(__file__).resolve().parent.parent
    pending = intent.pending_deliverables(root)
    assert "watcher/watcher.py" in pending


def test_is_progress_note():
    assert TaskIntentExtractor.is_progress_note("Mission complete: script verified")
    assert not TaskIntentExtractor.is_progress_note("import os\ndef main(): pass")


print("All task_intent tests passed.")
