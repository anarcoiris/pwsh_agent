"""Load queue job templates from knowledge/queue_templates/*.yaml."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from core.runtime_paths import app_root
from core.work_queue import enqueue_job


def templates_dir() -> Path:
    return app_root() / "knowledge" / "queue_templates"


def list_templates() -> list[str]:
    d = templates_dir()
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.yaml"))


def load_template(name: str) -> dict[str, Any]:
    path = templates_dir() / f"{name}.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"Template not found: {name}")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def enqueue_from_template(name: str, *, overrides: dict[str, Any] | None = None) -> str:
    """Load template by stem (e.g. hello_game) and enqueue."""
    data = load_template(name)
    if overrides:
        payload = {**(data.get("payload") or {}), **(overrides.get("payload") or {})}
        data = {**data, **{k: v for k, v in overrides.items() if k != "payload"}}
        data["payload"] = payload

    job_type = data.get("job_type")
    payload = data.get("payload") or {}
    if not job_type:
        raise ValueError(f"Template {name} missing job_type")

    return enqueue_job(
        job_type,
        payload,
        title=str(data.get("title", name)),
        priority=int(data.get("priority", 50)),
        cron_expr=data.get("cron_expr"),
        requires_idle_seconds=data.get("requires_idle_seconds"),
        night_start_hour=data.get("night_start_hour"),
        night_end_hour=data.get("night_end_hour"),
        checkpoint_profile=str(data.get("checkpoint_profile", "mvf_autonomous")),
    )
