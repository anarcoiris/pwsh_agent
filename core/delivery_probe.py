"""CPU probes for delivery claims in append_note lines (no LLM)."""

from __future__ import annotations

import re
from pathlib import Path

from core.runtime_paths import app_root

_CLAIM_RE = re.compile(
    r"\b(created|generated|wrote|saved|built|completed|finished|creó|creo|generó|genero)\b",
    re.I,
)
_PATH_IN_NOTE_RE = re.compile(
    r"(?:directory|folder|dir(?:ectorio)?|carpeta)\s+['\"]?([A-Za-z0-9_.-]+/?)['\"]?|"
    r"([A-Za-z][A-Za-z0-9_-]*/)",
    re.I,
)
_COUNT_CLAIM_RE = re.compile(
    r"\b(\d+)\s+(?:relatos|archivos|files|markdown|\.md)\b",
    re.I,
)


def note_claims_delivery(line: str) -> bool:
    text = (line or "").strip()
    if not text or not _CLAIM_RE.search(text):
        return False
    return bool(_PATH_IN_NOTE_RE.search(text) or _COUNT_CLAIM_RE.search(text))


def _glob_count(directory: Path, pattern: str) -> int:
    if not directory.is_dir():
        return 0
    return sum(1 for _ in directory.glob(pattern))


def probe_append_note_line(line: str, root: Path | None = None) -> str | None:
    """Return a SYSTEM warning if the note claims delivery without disk evidence."""
    text = (line or "").strip()
    if not note_claims_delivery(text):
        return None

    root = root or app_root()
    paths: list[str] = []
    for m in _PATH_IN_NOTE_RE.finditer(text):
        chunk = (m.group(1) or m.group(2) or "").strip().rstrip("/")
        if chunk:
            paths.append(chunk)

    count_match = _COUNT_CLAIM_RE.search(text)
    expected_count = int(count_match.group(1)) if count_match else None

    if paths:
        for rel in paths:
            target = (root / rel).resolve()
            if expected_count and expected_count > 1:
                n = _glob_count(target, "*.md") + _glob_count(target, "*")
                if not target.is_dir() or n < min(expected_count, 1):
                    return (
                        f"append_note claims delivery under '{rel}' but directory is missing "
                        f"or has fewer than expected artifacts (found {n}, note implies {expected_count}). "
                        "Use write_file/run_script and verify on disk before noting progress."
                    )
            elif not target.exists():
                return (
                    f"append_note claims delivery for '{rel}' but path does not exist on disk. "
                    "Verify with write_file or host_exec success before logging progress."
                )
        return None

    if expected_count and expected_count > 1:
        return (
            f"append_note claims {expected_count} files but no directory path was verified. "
            "Use write_file per artifact; bulk host_exec loops are discouraged for code_build."
        )
    return (
        "append_note claims delivery but no path was verified on disk. "
        "Confirm artifacts exist before logging progress."
    )
