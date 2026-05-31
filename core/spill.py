"""Pointer-first spill helper for oversized tool payloads."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

from core.session_paths import session_artifacts_dir


def _safe_name(tool_name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "_", (tool_name or "tool")).strip("_") or "tool"


def maybe_spill_text(
    session_id: str,
    tool_name: str,
    payload: str,
    *,
    threshold_chars: int = 18000,
    preview_chars: int = 1800,
) -> dict | None:
    """
    Spill large payloads to disk and return pointer metadata.
    Returns None if payload is under threshold.
    """
    text = payload or ""
    if len(text) <= threshold_chars:
        return None

    out_dir = session_artifacts_dir(session_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    base = _safe_name(tool_name)
    digest = hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()[:10]
    path = out_dir / f"{base}_{ts}_{digest}.txt"
    path.write_text(text, encoding="utf-8")

    preview = text[:preview_chars]
    line_count = text.count("\n") + (1 if text else 0)
    return {
        "artifact_file": str(path),
        "artifact_bytes": len(text.encode("utf-8", errors="replace")),
        "artifact_lines": line_count,
        "artifact_preview": preview,
        "artifact_note": (
            f"Full output spilled to {path}. "
            f"Use read_file(path=\"{path}\", line_start=1, line_count=120) for targeted retrieval."
        ),
    }
