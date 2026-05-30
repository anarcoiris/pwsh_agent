"""
core/task_intent.py — Parse user intent for deliverables and task type.

Used by chat_turn and WriteGuard to prevent substituting workspace/plan.md
for user-requested code deliverables (e.g. watcher/watcher.py).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


_WORKSPACE_META = frozenset({
    "workspace/plan.md",
    "workspace/status.md",
    "workspace/session_log.md",
})

_CODE_MARKERS = re.compile(
    r"(?m)^(\s*(import |from |def |class |function |#!<|Write-Host|\$\w+\s*=))",
    re.IGNORECASE,
)


@dataclass
class TaskIntent:
    deliverables: list[str] = field(default_factory=list)
    is_dev_task: bool = False
    forbid_network: bool = False

    def pending_deliverables(self, project_root: Path | None = None) -> list[str]:
        """Return deliverable paths that do not yet exist on disk."""
        pending: list[str] = []
        for rel in self.deliverables:
            p = Path(rel)
            if project_root and not p.is_absolute():
                p = project_root / rel
            if not p.exists():
                pending.append(rel.replace("\\", "/"))
        return pending


class TaskIntentExtractor:
    """Extract deliverable paths and constraints from a user message."""

    @classmethod
    def parse(cls, message: str) -> TaskIntent:
        msg = message or ""
        lower = msg.lower()

        deliverables = cls._extract_deliverables(msg)
        is_dev = bool(re.search(
            r"\b(write|script|python|\.py|\.ps1|file|folder|save|create|implement|code|watcher)\b",
            lower,
        ))
        forbid_network = bool(re.search(
            r"(do not|don't|no)\s+.*(network|recon|scan|port)|focus (?:only )?on",
            lower,
        ))

        return TaskIntent(
            deliverables=deliverables,
            is_dev_task=is_dev,
            forbid_network=forbid_network,
        )

    @classmethod
    def _extract_deliverables(cls, message: str) -> list[str]:
        found: list[str] = []
        lower = message.lower()

        folder_m = re.search(r"(?:in|to|under)\s+(?:the\s+)?([\w.-]+)\s+folder", lower)
        script_m = re.search(r"\b([\w.-]+\.(?:py|ps1))\b", message, re.I)

        if folder_m and script_m:
            found.append(f"{folder_m.group(1)}/{script_m.group(1)}")

        for m in re.finditer(r"([\w./\\-]+\.(?:py|ps1|md|txt))", message, re.I):
            path = m.group(1).replace("\\", "/")
            if path.startswith("workspace/plan") or path.startswith("workspace/status"):
                continue
            if path not in found:
                found.append(path)

        return found

    @staticmethod
    def is_workspace_meta_path(path: str) -> bool:
        normalized = path.replace("\\", "/").lower()
        if normalized in _WORKSPACE_META:
            return True
        return normalized.startswith("workspace/") and normalized.endswith(
            ("plan.md", "status.md", "session_log.md")
        )

    @staticmethod
    def is_progress_note(content: str) -> bool:
        """True if content looks like a status line, not source code."""
        if not content or len(content) > 500:
            return False
        if _CODE_MARKERS.search(content):
            return False
        if content.count("\n") > 8:
            return False
        return True
