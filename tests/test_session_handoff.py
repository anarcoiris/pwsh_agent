"""Session handoff seal and load tests."""

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.session_handoff import (
    format_handoff_for_state,
    load_handoff,
    seal_handoff,
)
from core.working_state import build_current_state


def test_seal_and_load_handoff():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        sid = "20260603_999999"
        state_dir = root / "state" / "sessions" / sid
        state_dir.mkdir(parents=True)
        (state_dir / "facts.json").write_text(
            '{"pcap":{"path":"last_capture.pcapng"},"artifacts":[{"path":"output/report_x.md"}]}',
            encoding="utf-8",
        )
        with patch("core.session_paths.app_root", return_value=root):
            with patch("core.session_handoff.session_state_dir", return_value=state_dir):
                path = seal_handoff(sid, outcome="partial")
                assert path is not None
                h = load_handoff(sid)
                assert h is not None
                assert h.get("session_id") == sid
                formatted = format_handoff_for_state(h)
                assert "session=" in formatted


def test_prior_handoff_in_current_state():
    block = build_current_state(
        mission="m",
        prior_handoff="session=20260603_212948\ndomain=web_auth\nsummary=login ok",
    )
    assert "[PRIOR HANDOFF]" in block
    assert "web_auth" in block
