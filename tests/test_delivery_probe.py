"""Tests for delivery_probe and R4b MVF exit helpers."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.delivery_probe import note_claims_delivery, probe_append_note_line
from core.mvf_validator import (
    derive_mvf_from_intent,
    mvf_exit_blocked,
    run_checks,
    save_mvf,
    validate_session,
)


def test_note_claims_delivery_detects_spanish():
    line = "Created directory ejemplos-texto and generated 100 relatos cortos."
    assert note_claims_delivery(line) is True


def test_probe_append_note_missing_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    warn = probe_append_note_line(
        "Created directory ejemplos-texto and generated 100 relatos cortos."
    )
    assert warn is not None
    assert "ejemplos-texto" in warn


def test_probe_append_note_ok_when_dir_has_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    d = tmp_path / "ejemplos-texto"
    d.mkdir()
    for i in range(3):
        (d / f"{i:02d}_x.md").write_text("x", encoding="utf-8")
    warn = probe_append_note_line(
        "Generated 3 relatos in ejemplos-texto/",
        root=tmp_path,
    )
    assert warn is None


def test_derive_mvf_dir_count_for_bulk_mission():
    text = (
        "Crea un directorio llamado ejemplos-texto/ y genera 100 relatos "
        "cortos guardalos como 01_titulo.md"
    )
    mvf = derive_mvf_from_intent(None, text)
    types = {c["type"] for c in mvf["checks"]}
    assert "dir_exists" in types
    assert "dir_count" in types
    dc = next(c for c in mvf["checks"] if c["type"] == "dir_count")
    assert dc["min_count"] == 100


def test_mvf_exit_blocked(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    save_mvf("sess_exit", {
        "checks": [{"type": "file_exists", "path": "missing.py"}],
        "validated": False,
    })
    blocked, failed = mvf_exit_blocked("sess_exit", {"mvf": {"enabled": True}})
    assert blocked is True
    assert failed


def test_dir_count_check(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    d = tmp_path / "ejemplos-texto"
    d.mkdir()
    (d / "01_a.md").write_text("a", encoding="utf-8")
    result = run_checks(
        [{"type": "dir_count", "path": "ejemplos-texto", "glob": "*.md", "min_count": 2}],
        root=tmp_path,
    )
    assert result.validated is False
