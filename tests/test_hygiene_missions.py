"""Tests for hygiene mission polling."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.hygiene_missions import poll_hygiene_missions


def test_poll_enqueues_mission(tmp_path, monkeypatch):
    feed = tmp_path / "feed"
    stub_dir = feed / "missions" / "test_repo"
    stub_dir.mkdir(parents=True)
    stub = stub_dir / "REF-001.mission"
    stub.write_text(
        json.dumps({
            "objective": "Fix REF-001 test mission",
            "specialist": "workspace",
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("HYGIENE_FEED_DIR", str(feed))

    cfg = Path(__file__).resolve().parent.parent / "config.yaml"
    import yaml
    original = cfg.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(original) or {}
        data.setdefault("hygiene_eyes", {})["feed_dir"] = str(feed)
        data["hygiene_eyes"]["poll_missions"] = True
        cfg.write_text(yaml.dump(data, allow_unicode=True), encoding="utf-8")

        count = poll_hygiene_missions()
        assert count == 1
        assert not stub.exists()
        assert (feed / "missions" / "done" / "test_repo" / "REF-001.mission").is_file()
    finally:
        cfg.write_text(original, encoding="utf-8")
