"""Tests for hygiene_lookup tool."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.hygiene import hygiene_lookup, resolve_feed_dir


def test_hygiene_lookup_missing_feed(tmp_path, monkeypatch):
    monkeypatch.setenv("HYGIENE_FEED_DIR", str(tmp_path / "empty"))
    result = hygiene_lookup(finding_id="REF-001")
    assert result["success"] is False
    assert "results" in result


def test_hygiene_lookup_finding_id(tmp_path, monkeypatch):
    feed = tmp_path / "feed"
    chunk_dir = feed / "findings" / "test_repo"
    chunk_dir.mkdir(parents=True)
    (chunk_dir / "REF-001.md").write_text(
        "---\nfinding_id: REF-001\n---\n\n# Unused import\n",
        encoding="utf-8",
    )
    manifest = {
        "findings": [{
            "repo": "test_repo",
            "finding_id": "REF-001",
            "severity": "P1",
            "auto_fixable": True,
            "title": "Unused import",
            "file": "core/parser.py",
            "line": 10,
            "task_id": "dead-code-scan",
            "source": "test",
            "chunk_path": "findings/test_repo/REF-001.md",
            "updated": "2026-06-18T00:00:00Z",
        }]
    }
    (feed / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setenv("HYGIENE_FEED_DIR", str(feed))

    result = hygiene_lookup(finding_id="REF-001")
    assert result["success"] is True
    assert result["count"] == 1
    assert result["results"][0]["finding_id"] == "REF-001"
    assert "Unused import" in result["results"][0]["excerpt"]
