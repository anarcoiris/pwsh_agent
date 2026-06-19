"""Tests for core/mvf_validator.py — CPU-only, no LLM."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.intent_spec import IntentSpec
from core.mvf_validator import (
    derive_mvf_from_intent,
    load_mvf,
    merge_mvf_override,
    mvf_path,
    run_checks,
    save_mvf,
    validate_session,
)


def test_file_exists_pass_fail(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "HelloGame" / "game.py"
    target.parent.mkdir(parents=True)
    target.write_text("print('hi')", encoding="utf-8")

    ok = run_checks([{"type": "file_exists", "path": "HelloGame/game.py"}], root=tmp_path)
    assert ok.validated is True

    missing = run_checks([{"type": "file_exists", "path": "HelloGame/missing.py"}], root=tmp_path)
    assert missing.validated is False


def test_command_check_echo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    if sys.platform == "win32":
        cmd = "py -3.10 -c print(1)"
    else:
        cmd = "python3 -c 'print(1)'"
    result = run_checks(
        [{"type": "command", "cmd": cmd, "exit_code": 0}],
        root=tmp_path,
    )
    assert result.validated is True


def test_derive_code_build_hello_game():
    spec = IntentSpec(
        domain="code_build",
        deliverables=["HelloGame/game.py", "HelloGame/PLAN.md"],
        raw="In HelloGame/ create game and tests. Run pytest.",
    )
    mvf = derive_mvf_from_intent(spec, spec.raw)
    paths = {c["path"] for c in mvf["checks"] if c["type"] == "file_exists"}
    assert "HelloGame/game.py" in paths
    assert any(c["type"] == "command" and "pytest" in c["cmd"] for c in mvf["checks"])


def test_merge_mvf_override():
    base = {"deliverables": ["a.py"], "checks": [{"type": "file_exists", "path": "a.py"}]}
    override = {
        "deliverables": ["HelloGame/game.py"],
        "checks": [{"type": "file_exists", "path": "HelloGame/game.py"}],
    }
    merged = merge_mvf_override(base, override)
    assert merged["deliverables"] == ["HelloGame/game.py"]
    assert merged["derived_from"] == "template_override"


def test_validate_session_persists(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    session_id = "test_mvf_sess"
    game = tmp_path / "HelloGame" / "game.py"
    game.parent.mkdir(parents=True)
    game.write_text("x = 1\n", encoding="utf-8")

    save_mvf(session_id, {
        "deliverables": ["HelloGame/game.py"],
        "checks": [{"type": "file_exists", "path": "HelloGame/game.py"}],
        "validated": False,
    })

    result = validate_session(session_id, root=tmp_path)
    assert result.validated is True

    data = json.loads(mvf_path(session_id).read_text(encoding="utf-8"))
    assert data["validated"] is True
    assert data["last_results"]


def test_command_check_pytest_mini_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    pkg = tmp_path / "HelloGame"
    tests = pkg / "tests"
    tests.mkdir(parents=True)
    (pkg / "game.py").write_text("def greet():\n    return 'hi'\n", encoding="utf-8")
    (tests / "test_game.py").write_text("def test_smoke():\n    assert True\n", encoding="utf-8")

    result = run_checks(
        [{
            "type": "command",
            "cmd": "py -3.10 -m pytest HelloGame/tests -q",
            "exit_code": 0,
        }],
        root=tmp_path,
    )
    assert result.validated is True
