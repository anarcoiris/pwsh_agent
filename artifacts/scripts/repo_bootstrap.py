"""Bootstrap sys.path for standalone scripts under artifacts/scripts/."""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]


def bootstrap() -> Path:
    root = str(_REPO_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    return _REPO_ROOT
