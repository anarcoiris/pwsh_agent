"""Tests for multi append_note extraction in the parser."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.parser import _pick_best_tool_calls


def _tc(name: str) -> dict:
    return {"function": {"name": name, "arguments": {}}}


def test_pick_returns_all_notes_plus_one_action():
    calls = [
        _tc("append_note"),
        _tc("append_note"),
        _tc("port_scan"),
        _tc("sequentialthinking"),
    ]
    picked = _pick_best_tool_calls(calls)
    names = [c["function"]["name"] for c in picked]
    assert names.count("append_note") == 2
    assert "port_scan" in names
    assert "sequentialthinking" not in names


def test_pick_notes_only_when_no_action():
    calls = [_tc("append_note"), _tc("append_note")]
    picked = _pick_best_tool_calls(calls)
    assert len(picked) == 2
    assert all(c["function"]["name"] == "append_note" for c in picked)


if __name__ == "__main__":
    test_pick_returns_all_notes_plus_one_action()
    test_pick_notes_only_when_no_action()
    print("ok")
