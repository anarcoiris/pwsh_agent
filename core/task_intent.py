"""
core/task_intent.py — Parse user intent for deliverables and task type.

Used by chat_turn and WriteGuard to prevent substituting workspace/plan.md
for user-requested code deliverables (e.g. watcher/watcher.py).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

MissionKind = Literal["hash", "pcap", "dev", "recon", "general"]


def _is_credential_deliverable(rel_path: str) -> bool:
    lower = Path(rel_path.replace("\\", "/")).name.lower()
    if lower in ("pwd.txt", "login_forms.txt", "credentials.txt"):
        return True
    if re.match(r"pwd(?:_[\d]+)?\.txt$", lower):
        return True
    return "login_forms" in lower


def path_matches_deliverable(path: str, deliverables: list[str]) -> bool:
    """True when write path matches one of the user-requested deliverable paths."""
    if not deliverables:
        return True
    norm = path.replace("\\", "/").lower()
    basename = Path(norm).name
    for d in deliverables:
        d_norm = d.replace("\\", "/").lower()
        if norm == d_norm or norm.endswith("/" + d_norm):
            return True
        if Path(d_norm).name == basename:
            return True
    return False


def _pwd_file_is_placeholder(path: Path) -> bool:
    try:
        if not path.exists() or not path.is_file():
            return False
        from core.task_plan import _looks_like_placeholder_file
        return _looks_like_placeholder_file(path.read_text(encoding="utf-8", errors="replace")[:500])
    except OSError:
        return False


_WORKSPACE_META = frozenset({
    "workspace/plan.md",
    "workspace/status.md",
    "workspace/session_log.md",
})

_CODE_MARKERS = re.compile(
    r"(?m)^(\s*(import |from |def |class |function |#!<|Write-Host|\$\w+\s*=))",
    re.IGNORECASE,
)


def detect_mission_kind(text: str) -> MissionKind:
    """Classify mission from user text (shared with DynamicContextBuilder)."""
    lower = (text or "").lower()
    if re.search(
        r"(crack.*(?:sha-?)?256|(?:sha-?)?256.*hash|hash.*crack|brute.*force|\bcrack_hash\b|"
        r"\bhaspro\b|\bhashpro\b)",
        lower,
    ):
        return "hash"
    if re.search(
        r"(\.pcapng|\.pcap\b|\btshark\b|\bwireshark\b|last_capture|decode.*packet|"
        r"analyze.*packet|http packet|login.*packet)",
        lower,
    ):
        return "pcap"
    skip_network = bool(re.search(
        r"(do not|don't|no)\s+.*(network|recon|scan|port)|focus (?:only )?on|watcher\.py",
        lower,
    ))
    dev_task = bool(re.search(
        r"\b(write|script|python|\.py|\.ps1|\.md|folder|save|create|implement|code|watcher)\b",
        lower,
    )) or (
        bool(re.search(r"\b(read|review)\b", lower))
        and not bool(re.search(r"\bfile named\b", lower))
    )
    explicit_recon = bool(re.search(
        r"\b(scan|recon|capture|pcap|cve|dns|ping|port_scan|network interface)\b",
        lower,
    ))
    if (skip_network or dev_task) and not explicit_recon:
        return "dev"
    if explicit_recon:
        return "recon"
    return "general"


@dataclass
class TaskIntent:
    deliverables: list[str] = field(default_factory=list)
    is_dev_task: bool = False
    forbid_network: bool = False
    mission_kind: MissionKind = "general"

    def pending_deliverables(self, workspace_root: Path | None = None) -> list[str]:
        """Return deliverable paths that do not yet exist on disk."""
        pending: list[str] = []
        for rel in self.deliverables:
            rel_norm = rel.replace("\\", "/")
            candidates: list[Path] = [Path(rel_norm)]
            if workspace_root and not Path(rel_norm).is_absolute():
                candidates.extend([
                    workspace_root / rel_norm,
                    workspace_root / "workspace" / Path(rel_norm).name,
                    workspace_root.parent / rel_norm if workspace_root.name == "workspace" else workspace_root / rel_norm,
                ])
            # Also check app-relative workspace/ prefix
            candidates.append(Path("workspace") / Path(rel_norm).name)

            found = False
            for p in candidates:
                try:
                    if p.exists() and p.is_file() and p.stat().st_size > 0:
                        if _is_credential_deliverable(rel_norm) and _pwd_file_is_placeholder(p):
                            found = False
                            break
                        found = True
                        break
                except OSError:
                    continue
            if not found:
                pending.append(rel_norm)
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
            mission_kind=detect_mission_kind(msg),
        )

    @classmethod
    def _extract_deliverables(cls, message: str) -> list[str]:
        found: list[str] = []
        lower = message.lower()

        folder_m = re.search(r"(?:in|to|under)\s+(?:the\s+)?([\w.-]+)\s+folder", lower)
        script_m = re.search(r"\b([\w.-]+\.(?:py|ps1))\b", message, re.I)

        if folder_m and script_m:
            found.append(f"{folder_m.group(1)}/{script_m.group(1)}")

        named_m = re.search(
            r"file\s+named\s+['\"]?([^'\"]+\.(?:txt|md|py|ps1))['\"]?",
            message,
            re.I,
        )
        if named_m:
            found.append(named_m.group(1).replace("\\", "/"))

        for m in re.finditer(r"([\w./\\-]+\.(?:py|ps1|md|txt))", message, re.I):
            path = m.group(1).replace("\\", "/")
            if path.startswith("workspace/plan") or path.startswith("workspace/status"):
                continue
            if path not in found:
                found.append(path)

        return cls._dedupe_deliverables(found)

    @staticmethod
    def _dedupe_deliverables(found: list[str]) -> list[str]:
        """Drop bare filenames when a qualified path with the same basename exists."""
        normalized = [p.replace("\\", "/") for p in found]
        qualified_basenames = {
            Path(p).name for p in normalized if "/" in p or "\\" in p
        }
        result: list[str] = []
        for path in normalized:
            if "/" not in path and path in qualified_basenames:
                continue
            if path not in result:
                result.append(path)
        return result

    @staticmethod
    def is_workspace_meta_path(path: str) -> bool:
        from core.session_paths import is_session_note_path
        normalized = path.replace("\\", "/").lower()
        if normalized in _WORKSPACE_META:
            return True
        if is_session_note_path(path):
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
