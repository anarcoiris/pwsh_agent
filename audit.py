"""
audit.py - Lightweight HMAC-signed audit trail for Pulse Windows Agent.

Every tool invocation writes a JSON line to audit_trail/YYYY-MM-DD.jsonl.
Each entry is signed with HMAC-SHA256 to detect tampering.
"""
import hashlib
import hmac
import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("pwsh_agent.audit")

# Secret key — override via env var AUDIT_HMAC_SECRET
_HMAC_SECRET = os.environ.get("AUDIT_HMAC_SECRET", "pulse-default-secret-change-me").encode()
_PROJECT_ROOT = Path(__file__).resolve().parent
_AUDIT_DIR = _PROJECT_ROOT / "audit_trail"


@dataclass
class AuditEntry:
    method: str
    params: dict
    status: str                         # "success" | "error" | "skipped"
    result_hash: Optional[str] = None
    error: Optional[str] = None
    specialist: str = "lead"
    network_mode: str = "SANDBOX"
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    duration_ms: Optional[int] = None
    # Computed on write
    signature: str = ""


class AuditTrail:
    """Append-only HMAC-signed audit log, one file per day."""

    def __init__(self):
        _AUDIT_DIR.mkdir(parents=True, exist_ok=True)

    def _log_path(self) -> Path:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return _AUDIT_DIR / f"{day}.jsonl"

    def _sign(self, payload: dict) -> str:
        """Returns HMAC-SHA256 hex digest of the serialized payload (sans signature field)."""
        payload_clean = {k: v for k, v in payload.items() if k != "signature"}
        raw = json.dumps(payload_clean, sort_keys=True, default=str).encode()
        return hmac.new(_HMAC_SECRET, raw, hashlib.sha256).hexdigest()

    def record(self, entry: AuditEntry) -> None:
        """Write a signed entry to today's audit log."""
        payload = asdict(entry)
        payload["signature"] = self._sign(payload)
        line = json.dumps(payload, default=str)
        try:
            with open(self._log_path(), "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception as e:
            logger.warning(f"Audit write failed: {e}")

    def verify(self) -> dict:
        """
        Verify integrity of all entries in today's log.
        Returns a dict with 'total', 'valid', 'tampered' counts and
        a list of tampered entry timestamps.
        """
        path = self._log_path()
        if not path.exists():
            return {"total": 0, "valid": 0, "tampered": 0, "tampered_entries": []}

        total = valid = tampered = 0
        tampered_entries = []

        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                    stored_sig = payload.get("signature", "")
                    expected_sig = self._sign(payload)
                    total += 1
                    if hmac.compare_digest(stored_sig, expected_sig):
                        valid += 1
                    else:
                        tampered += 1
                        tampered_entries.append(payload.get("timestamp", "unknown"))
                except Exception:
                    tampered += 1

        return {
            "total": total,
            "valid": valid,
            "tampered": tampered,
            "tampered_entries": tampered_entries,
        }

    def recent(self, n: int = 20) -> list:
        """Return the last N audit entries from today's log (unsigned dicts)."""
        path = self._log_path()
        if not path.exists():
            return []
        lines = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        lines.append(json.loads(line))
                    except Exception:
                        pass
        return lines[-n:]


# Module-level singleton
_audit = AuditTrail()


def get_audit() -> AuditTrail:
    return _audit
