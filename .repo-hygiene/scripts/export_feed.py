#!/usr/bin/env python3
"""Export repo-hygiene findings to a neutral feed directory for pwsh_agent."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

HYGIENE_DIR = Path(__file__).parent.parent
CONFIG_FILE = HYGIENE_DIR / "config.yaml"

_SEVERITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
_FINDING_ID_RE = re.compile(r"^(REF|DOC|DEP|ARCH)-\d+", re.I)
_AI_TAG_RE = re.compile(r"^\s*-\s*\[([A-Z0-9-]+)\]\s*(.+)$", re.M)


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        return {}
    with open(CONFIG_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def resolve_feed_dir(config: dict | None = None) -> Path:
    config = config or load_config()
    feed_cfg = config.get("feed", {})
    raw = os.environ.get("HYGIENE_FEED_DIR") or feed_cfg.get("dir", "")
    if not raw:
        raw = str(HYGIENE_DIR.parent / "Libraries" / "hygiene-feed")
    return Path(raw).resolve()


def _slug_repo(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", name).strip("_") or "unknown"


def _finding_chunk_md(
    *,
    repo: str,
    finding: dict,
    task_id: str,
    source: str,
    updated: str,
) -> str:
    fid = finding.get("id", "UNK-000")
    severity = finding.get("severity", "P2")
    auto_fix = bool(finding.get("auto_fixable", False))
    title = finding.get("title", "Hygiene finding")
    file_path = finding.get("file", "")
    line = finding.get("line", "")
    action = finding.get("suggested_action", finding.get("description", ""))
    loc = f"{file_path}:{line}" if line else file_path

    return f"""---
repo: {repo}
finding_id: {fid}
severity: {severity}
auto_fixable: {str(auto_fix).lower()}
tools: [hygiene_lookup, read_file, write_file, grep_file, run_script]
phase: [hygiene, development]
task_id: {task_id}
updated: {updated}
---

# {fid}: {title}

**File:** {loc}
**Action:** {action}
**Source:** {source}
"""


def _manifest_entry(
    *,
    repo: str,
    finding: dict,
    chunk_path: Path,
    task_id: str,
    source: str,
    updated: str,
) -> dict:
    fid = finding.get("id", "UNK-000")
    return {
        "repo": repo,
        "finding_id": fid,
        "severity": finding.get("severity", "P2"),
        "auto_fixable": bool(finding.get("auto_fixable", False)),
        "title": finding.get("title", ""),
        "file": finding.get("file", ""),
        "line": finding.get("line"),
        "task_id": task_id,
        "source": source,
        "chunk_path": str(chunk_path.as_posix()),
        "updated": updated,
    }


def _should_mission(finding: dict) -> bool:
    if not finding.get("auto_fixable"):
        return False
    sev = finding.get("severity", "P3")
    return sev in ("P0", "P1")


def _collect_orchestrator_findings(
    repo_path: Path,
    entries: list[dict],
    chunks: list[tuple[Path, str]],
    missions: list[tuple[Path, dict]],
) -> int:
    reports_root = repo_path / ".reports" / "hygiene"
    if not reports_root.exists():
        return 0

    count = 0
    repo_slug = _slug_repo(repo_path.name)
    updated = datetime.now(timezone.utc).isoformat()

    for date_dir in sorted(reports_root.iterdir(), reverse=True):
        if not date_dir.is_dir():
            continue
        for task_dir in date_dir.iterdir():
            if not task_dir.is_dir():
                continue
            task_id = task_dir.name
            for json_file in task_dir.glob("*.json"):
                try:
                    data = json.loads(json_file.read_text(encoding="utf-8"))
                except Exception:
                    continue
                for finding in data.get("findings", []):
                    fid = finding.get("id")
                    if not fid:
                        continue
                    source = f"repo-hygiene/{task_id}"
                    chunk_rel = Path("findings") / repo_slug / f"{fid}.md"
                    body = _finding_chunk_md(
                        repo=repo_slug,
                        finding=finding,
                        task_id=task_id,
                        source=source,
                        updated=updated,
                    )
                    chunks.append((chunk_rel, body))
                    entries.append(
                        _manifest_entry(
                            repo=repo_slug,
                            finding=finding,
                            chunk_path=chunk_rel,
                            task_id=task_id,
                            source=source,
                            updated=updated,
                        )
                    )
                    if _should_mission(finding):
                        mission_rel = Path("missions") / repo_slug / f"{fid}.mission"
                        missions.append((
                            mission_rel,
                            {
                                "repo_path": str(repo_path.resolve()),
                                "objective": (
                                    f"Fix {fid} per hygiene feed: {finding.get('title', '')}. "
                                    f"Use hygiene_lookup then workspace tools."
                                ),
                                "specialist": "workspace",
                                "source_finding": fid,
                            },
                        ))
                    count += 1
    return count


def _parse_ai_review_md(path: Path, repo_slug: str) -> list[dict]:
    """Extract deterministic tag lines from ai_review markdown as pseudo-findings."""
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8", errors="ignore")
    findings: list[dict] = []
    idx = 1
    for m in _AI_TAG_RE.finditer(text):
        tag, detail = m.group(1), m.group(2).strip()
        file_part = ""
        line_part = None
        if ":" in detail:
            parts = detail.split(":", 1)
            file_part = parts[0].strip()
            rest = parts[1].strip()
            if rest.split()[0].isdigit():
                line_part = int(rest.split()[0])
        findings.append({
            "id": f"AI-{tag}-{idx:03d}",
            "category": tag.lower(),
            "file": file_part,
            "line": line_part,
            "severity": "P2",
            "title": f"[{tag}] {detail[:120]}",
            "description": detail,
            "suggested_action": f"Review and remediate: {detail}",
            "auto_fixable": False,
        })
        idx += 1
    return findings


def export_feed(
    *,
    feed_dir: Path | None = None,
    repo_paths: list[Path] | None = None,
    include_ai_review: bool = True,
    hygiene_hub_dir: Path | None = None,
) -> dict:
    config = load_config()
    feed_dir = feed_dir or resolve_feed_dir(config)
    hub = hygiene_hub_dir or HYGIENE_DIR

    if repo_paths is None:
        repo_paths = []
        hub_cfg = config.get("hub", {})
        scan_root = (hub / hub_cfg.get("scan_root", "..")).resolve()
        exclude = set(hub_cfg.get("exclude_dirs", []))
        if scan_root.exists():
            for child in scan_root.iterdir():
                if child.is_dir() and child.name not in exclude and not child.name.startswith("."):
                    if (child / ".git").exists() or (child / ".reports").exists():
                        repo_paths.append(child)

    entries: list[dict] = []
    chunks: list[tuple[Path, str]] = []
    missions: list[tuple[Path, dict]] = []
    updated = datetime.now(timezone.utc).isoformat()

    for repo_path in repo_paths:
        repo_path = repo_path.resolve()
        repo_slug = _slug_repo(repo_path.name)
        count = _collect_orchestrator_findings(repo_path, entries, chunks, missions)

        if include_ai_review:
            ai_dir = hub / ".reports" / "ai_review"
            safe_name = repo_path.name.replace(" ", "_").replace("(", "").replace(")", "")
            ai_file = ai_dir / f"{safe_name}_ai_review.md"
            for finding in _parse_ai_review_md(ai_file, repo_slug):
                source = "repo-hygiene/ai_review"
                fid = finding["id"]
                chunk_rel = Path("findings") / repo_slug / f"{fid}.md"
                body = _finding_chunk_md(
                    repo=repo_slug,
                    finding=finding,
                    task_id="ai_review",
                    source=source,
                    updated=updated,
                )
                chunks.append((chunk_rel, body))
                entries.append(
                    _manifest_entry(
                        repo=repo_slug,
                        finding=finding,
                        chunk_path=chunk_rel,
                        task_id="ai_review",
                        source=source,
                        updated=updated,
                    )
                )
                count += 1

    feed_dir.mkdir(parents=True, exist_ok=True)
    for rel, body in chunks:
        out = feed_dir / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(body, encoding="utf-8")

    missions_dir = feed_dir / "missions"
    missions_dir.mkdir(parents=True, exist_ok=True)
    for rel, payload in missions:
        out = feed_dir / rel
        if not out.exists():
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    entries.sort(key=lambda e: (_SEVERITY_ORDER.get(e.get("severity", "P3"), 9), e.get("finding_id", "")))
    manifest = {
        "updated": updated,
        "count": len(entries),
        "findings": entries,
    }
    (feed_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return {"feed_dir": str(feed_dir), "findings": len(entries), "missions": len(missions)}


def maybe_export_after_run(repo_path: Path | None = None) -> None:
    """Called after hygiene tasks if feed.export_on_complete is true."""
    config = load_config()
    feed_cfg = config.get("feed", {})
    if not feed_cfg.get("export_on_complete", True):
        return
    paths = [repo_path] if repo_path else None
    try:
        result = export_feed(repo_paths=paths)
        print(f"[export_feed] Wrote {result['findings']} findings to {result['feed_dir']}")
    except Exception as exc:
        print(f"[export_feed] Warning: export failed: {exc}", file=sys.stderr)


def main() -> None:
    if sys.platform.startswith("win"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except AttributeError:
            pass

    parser = argparse.ArgumentParser(description="Export hygiene findings to neutral feed dir")
    parser.add_argument("--repo", action="append", help="Repo path to export (repeatable)")
    parser.add_argument("--feed-dir", help="Override feed directory")
    parser.add_argument("--no-ai-review", action="store_true")
    args = parser.parse_args()

    repo_paths = [Path(p).resolve() for p in args.repo] if args.repo else None
    feed_dir = Path(args.feed_dir).resolve() if args.feed_dir else None
    result = export_feed(
        feed_dir=feed_dir,
        repo_paths=repo_paths,
        include_ai_review=not args.no_ai_review,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
