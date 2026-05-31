"""
core/memory.py — Handles daily execution logs and long-term memory updates.

Satisfies AGENTS.md requirements:
- Store all daily execution logs under memory/YYYY-MM-DD.md
- Keep the curated long-term system status inside MEMORY.md
"""

import os
from datetime import datetime, timezone
from pathlib import Path

from core.runtime_paths import app_root

_MEMORY_DIR = app_root() / "state" / "memory"
_MEMORY_FILE = app_root() / "state" / "MEMORY.md"


def log_daily_execution(
    session_id: str,
    specialist: str,
    prompt: str,
    steps_count: int,
    findings_count: int,
    outcome: str
) -> None:
    """Log an execution turn to memory/YYYY-MM-DD.md."""
    try:
        _MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        today = datetime.now().strftime("%Y-%m-%d")
        log_file = _MEMORY_DIR / f"{today}.md"
        
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        # Strip long outcome responses for log brevity
        short_outcome = outcome.strip()
        if len(short_outcome) > 500:
            short_outcome = short_outcome[:500] + "\n... (truncated for brevity) ..."
            
        log_entry = (
            f"### 🕒 [{timestamp}] Session: `{session_id}` | Specialist: `{specialist.upper()}`\n"
            f"- **Objective/Prompt**: {prompt}\n"
            f"- **Steps Executed**: {steps_count}\n"
            f"- **New Findings Persisted**: {findings_count}\n"
            f"- **Outcome Summary**:\n"
            f"```markdown\n"
            f"{short_outcome}\n"
            f"```\n"
            f"\n---\n\n"
        )
        
        # Write to daily log
        first_write = not log_file.exists()
        with open(log_file, "a", encoding="utf-8") as f:
            if first_write:
                f.write(f"# 📅 Daily Execution Log — {today}\n\n")
            f.write(log_entry)
            
        # Curate and update MEMORY.md if needed
        update_long_term_status(today, session_id, specialist, findings_count)
            
    except Exception as e:
        # Avoid crashing core loop if memory logging fails
        import logging
        logging.getLogger("pwsh_agent.core.memory").warning("Memory log error: %s", e)


def update_long_term_status(today: str, session_id: str, specialist: str, findings_count: int) -> None:
    """Updates recent milestones and stats inside MEMORY.md."""
    try:
        import re
        if not _MEMORY_FILE.exists():
            return
            
        content = _MEMORY_FILE.read_text(encoding="utf-8")
        
        # Count the number of daily logs to show total engagement days
        log_days = len(list(_MEMORY_DIR.glob("*.md")))
        
        # Build standard statistical block or append to milestones
        stat_line = f"- **Last Run**: {today} (Session: `{session_id}`, Persona: `{specialist.upper()}`)"
        
        # Check if the milestone section is present and update stats
        if "## 📊 System Operations & Stats" not in content:
            stats_block = (
                f"\n## 📊 System Operations & Stats\n"
                f"{stat_line}\n"
                f"- **Total Auditing Days**: {log_days}\n"
            )
            content += stats_block
        else:
            # Replace the stats block
            pattern = r"## 📊 System Operations & Stats.*"
            stats_block = (
                f"## 📊 System Operations & Stats\n"
                f"{stat_line}\n"
                f"- **Total Auditing Days**: {log_days}"
            )
            content = re.sub(pattern, stats_block, content, flags=re.DOTALL)
            
        with open(_MEMORY_FILE, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception:
        pass


def raw_write_memory_md(content: str) -> None:
    """Helper to update MEMORY.md contents safely."""
    try:
        with open(_MEMORY_FILE, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception:
        pass
