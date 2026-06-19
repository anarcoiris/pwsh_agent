"""Poll hygiene-feed mission stubs and enqueue pwsh_agent scheduled missions."""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

import yaml

from core.runtime_paths import app_root
from core.scheduler import schedule_mission

logger = logging.getLogger("pwsh_agent.core.hygiene_missions")


def _load_config() -> dict:
    cfg_path = app_root() / "config.yaml"
    if not cfg_path.exists():
        return {}
    with open(cfg_path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def resolve_feed_dir() -> Path:
    import os

    config = _load_config()
    eyes = config.get("hygiene_eyes", {})
    raw = os.environ.get("HYGIENE_FEED_DIR") or eyes.get("feed_dir", "")
    if not raw:
        raw = str(app_root().parent / "hygiene-feed")
    return Path(raw).resolve()


def poll_hygiene_missions() -> int:
    """Import .mission stubs from feed into scheduler; move stubs to done/."""
    config = _load_config()
    eyes = config.get("hygiene_eyes", {})
    if not eyes.get("poll_missions", True):
        return 0

    feed_dir = resolve_feed_dir()
    missions_root = feed_dir / "missions"
    if not missions_root.exists():
        return 0

    done_root = missions_root / "done"
    done_root.mkdir(parents=True, exist_ok=True)
    enqueued = 0

    for stub in missions_root.rglob("*.mission"):
        if "done" in stub.parts:
            continue
        try:
            payload = json.loads(stub.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Skipping invalid mission stub %s: %s", stub, exc)
            continue

        objective = payload.get("objective", "").strip()
        if not objective:
            continue

        specialist = payload.get("specialist", "workspace")
        schedule_mission(
            mission_text=objective,
            cron_expr=None,
            specialist=specialist,
            network_mode="SANDBOX",
        )
        enqueued += 1

        rel = stub.relative_to(missions_root)
        dest = done_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(stub), str(dest))
        logger.info("Enqueued hygiene mission from %s", stub.name)

    return enqueued
