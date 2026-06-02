"""
scripts/reset_state.py — archive + purge runtime state for a clean installation.

The agent accumulates a lot of volatile data over time (session histories,
roadmaps, daily memory, audit trails, PCAP decode logs, generated reports).
This utility archives that data to a single timestamped zip and then wipes it
so the next launch starts from a clean slate.

Modes (default: keep-identity)
  keep-identity         Archive + wipe ONLY generated data. Preserve identity
                        files, MEMORY.md, config.yaml, and findings.db.
  keep-identity-wipe-db Same as keep-identity, plus archive + delete
                        state/findings.db (recreated empty on next launch).
  factory-reset         Same as keep-identity-wipe-db, plus archive the identity
                        files and replace them with neutral stub templates.

Safety
  - Dry-run by default. Nothing is touched without --apply.
  - With --apply you are prompted for confirmation unless --yes is given.
  - Everything that gets wiped is archived FIRST (to the system temp dir), and
    the archive is only moved into the repo after the purge completes, so the
    purge can never delete the archive it just created.

Usage
  python scripts/reset_state.py                      # dry-run, mode keep-identity
  python scripts/reset_state.py --apply              # archive + purge (prompts)
  python scripts/reset_state.py --mode keep-identity-wipe-db --apply --yes
  python scripts/reset_state.py --mode factory-reset --apply
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# ── What counts as generated/volatile data (wiped in every mode) ──────────────
# Directories whose entire contents are runtime/generated.
GENERATED_DIRS = [
    "state/sessions",
    "state/memory",
    "state/audit_trail",
    ".pulse",
    "output",
    "workspace",          # fully gitignored runtime (deliverables, session notes)
    "artifacts/archived",  # previous archive backups
]

# Individual stray files that accumulate at known paths.
GENERATED_FILES = [
    "watcher/log_initial.txt",
    "capture.pcapng",
    "test_verify.pcapng",
]

# Glob patterns (relative to repo root) for ephemeral dirs like tmp123/.
GENERATED_GLOBS = ["tmp*"]

# Files reset to a minimal default rather than deleted.
RESET_FILES = {"state/active_session.json": "{}\n"}

# Directories recreated empty after purge so the agent has its expected layout.
RECREATE_DIRS = [
    "state/sessions",
    "state/memory",
    "state/audit_trail",
    "workspace",
    "artifacts/archived",
]

# Preserved in keep-identity; archived + wiped in higher modes.
DB_FILE = "state/findings.db"
IDENTITY_FILES = [
    "state/SOUL.md",
    "state/IDENTITY.md",
    "state/USER.md",
    "state/MEMORY.md",
]

# Neutral identity stubs written by factory-reset.
NEUTRAL_STUBS = {
    "state/SOUL.md": (
        "# SOUL\n\n"
        "You are a capable, general-purpose local Windows operator. You help with "
        "development, scripting, system administration, file and code review, "
        "analysis, and security tasks. Choose tools that fit the request; do not "
        "assume any single domain.\n\n"
        "## Principles\n"
        "- Understand the user's actual intent before acting.\n"
        "- Prefer the simplest tool that accomplishes the goal.\n"
        "- Be clear, structured, and honest about uncertainty.\n"
        "- Ask before destructive or irreversible actions.\n"
    ),
    "state/IDENTITY.md": (
        "# IDENTITY\n\n"
        "Name: (unset)\n"
        "Role: General-purpose Windows operator and coding assistant.\n"
        "Scope: development, sysadmin, analysis, file/code review, security.\n"
    ),
    "state/USER.md": (
        "# USER\n\n"
        "(No user profile yet. Populate as you learn the operator's preferences.)\n"
    ),
    "state/MEMORY.md": (
        "# MEMORY\n\n"
        "(Fresh long-term memory. Curated learnings go here.)\n"
    ),
}

MODES = ("keep-identity", "keep-identity-wipe-db", "factory-reset")


def _resolve(rel: str) -> Path:
    return (REPO_ROOT / rel).resolve()


def collect_targets(mode: str) -> tuple[list[Path], list[Path]]:
    """Return (paths_to_archive_and_remove, identity_paths_to_stub).

    Identity paths are archived AND removed, then replaced with neutral stubs.
    """
    targets: list[Path] = []

    for rel in GENERATED_DIRS:
        p = _resolve(rel)
        if p.is_dir():
            targets.append(p)
    for rel in GENERATED_FILES:
        p = _resolve(rel)
        if p.is_file():
            targets.append(p)
    for pattern in GENERATED_GLOBS:
        for p in REPO_ROOT.glob(pattern):
            # Only sweep ephemeral *directories* (e.g. tmpXXXX/), never files.
            if p.is_dir():
                targets.append(p.resolve())

    if mode in ("keep-identity-wipe-db", "factory-reset"):
        db = _resolve(DB_FILE)
        if db.is_file():
            targets.append(db)

    identity: list[Path] = []
    if mode == "factory-reset":
        for rel in IDENTITY_FILES:
            p = _resolve(rel)
            if p.is_file():
                identity.append(p)

    # De-dup while preserving order.
    seen: set[Path] = set()
    deduped: list[Path] = []
    for p in targets + identity:
        if p not in seen:
            seen.add(p)
            deduped.append(p)
    return deduped, identity


def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(REPO_ROOT)).replace("\\", "/")
    except ValueError:
        return str(p)


def _iter_files(path: Path):
    if path.is_file():
        yield path
    elif path.is_dir():
        for child in path.rglob("*"):
            if child.is_file():
                yield child


def build_archive(targets: list[Path], mode: str) -> Path:
    """Zip every target into a temp file outside the repo; return its path."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    tmp_zip = Path(tempfile.gettempdir()) / f"clean_install_{ts}_{mode}.zip"
    with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for target in targets:
            for f in _iter_files(target):
                zf.write(f, arcname=_rel(f))
    return tmp_zip


def purge(targets: list[Path]) -> None:
    for p in targets:
        try:
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            elif p.is_file():
                p.unlink()
        except OSError as exc:  # pragma: no cover - best-effort cleanup
            print(f"  ! could not remove {_rel(p)}: {exc}")


def recreate_layout(mode: str, identity: list[Path]) -> None:
    for rel in RECREATE_DIRS:
        _resolve(rel).mkdir(parents=True, exist_ok=True)
    for rel, content in RESET_FILES.items():
        p = _resolve(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    if mode == "factory-reset":
        for rel, content in NEUTRAL_STUBS.items():
            p = _resolve(rel)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")


def human_size(num: int) -> str:
    size = float(num)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Archive + purge agent runtime state.")
    parser.add_argument("--mode", choices=MODES, default="keep-identity")
    parser.add_argument("--apply", action="store_true", help="Actually archive + purge (default: dry-run).")
    parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt.")
    args = parser.parse_args(argv)

    targets, identity = collect_targets(args.mode)

    print(f"Repo root : {REPO_ROOT}")
    print(f"Mode      : {args.mode}")
    print(f"Apply     : {args.apply}")
    print()

    if not targets:
        print("Nothing to archive or purge — state is already clean.")
        return 0

    total_files = 0
    total_bytes = 0
    print("Targets (archived then wiped):")
    for p in targets:
        files = list(_iter_files(p))
        size = sum(f.stat().st_size for f in files if f.exists())
        total_files += len(files)
        total_bytes += size
        kind = "dir " if p.is_dir() else "file"
        suffix = "  [identity -> neutral stub]" if p in identity else ""
        print(f"  {kind} {_rel(p)}  ({len(files)} files, {human_size(size)}){suffix}")
    print(f"\nTotal: {total_files} files, {human_size(total_bytes)}")

    preserved = ["code", "config.yaml", "docs/", "knowledge/", "tests/", "artifacts/scripts/", ".venv/"]
    if args.mode == "keep-identity":
        preserved += ["identity files", "MEMORY.md", "findings.db"]
    elif args.mode == "keep-identity-wipe-db":
        preserved += ["identity files", "MEMORY.md"]
    print("Preserved : " + ", ".join(preserved))

    if not args.apply:
        print("\nDRY-RUN - no changes made. Re-run with --apply to execute.")
        return 0

    if not args.yes:
        reply = input("\nProceed with archive + purge? type 'yes' to continue: ").strip().lower()
        if reply != "yes":
            print("Aborted.")
            return 1

    print("\nArchiving...")
    archive_tmp = build_archive(targets, args.mode)
    print(f"  archive built: {archive_tmp} ({human_size(archive_tmp.stat().st_size)})")

    print("Purging...")
    purge(targets)

    print("Recreating clean layout...")
    recreate_layout(args.mode, identity)

    archived_dir = _resolve("artifacts/archived")
    archived_dir.mkdir(parents=True, exist_ok=True)
    final_archive = archived_dir / archive_tmp.name
    shutil.move(str(archive_tmp), str(final_archive))

    print("\nDone.")
    print(f"  archive : {_rel(final_archive)}")
    print(f"  mode    : {args.mode}")
    print("  A fresh runtime state will be created on next agent launch.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
