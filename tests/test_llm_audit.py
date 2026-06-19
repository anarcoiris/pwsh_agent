"""Regression: llm_audit modes (full/meta/off), token telemetry, viewer rendering."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from core import debug_log  # noqa: E402
from core.session_paths import sessions_state_root  # noqa: E402
from tools_dev.llm_audit_view import render_exchange, summarize_exchange  # noqa: E402

SESSION = "test_llm_audit"
MESSAGES = [
    {"role": "system", "content": "### CURRENT STATE ###\nplan: fetch page"},
    {"role": "user", "content": "login to the router"},
]


def _audit_path() -> Path:
    return sessions_state_root() / SESSION / "llm_audit.jsonl"


def _log(mode: str) -> None:
    debug_log.log_llm_interaction(
        model="qwen-test",
        latency_ms=123,
        messages=MESSAGES,
        response_text="<tool_call>{\"name\": \"http_get\"}</tool_call>",
        tools_schema=[{"type": "function"}],
        mode=mode,
        prompt_eval_count=7800,
        eval_count=150,
        total_duration_ms=4000,
        num_ctx=8192,
        native_tool_calls=0,
        parsed_tool_calls=1,
        parser_paths=["xml_tag"],
    )


def main() -> int:
    # Redirect active session for the test
    orig_loader = debug_log.load_active_session_id
    debug_log.load_active_session_id = lambda: SESSION  # type: ignore
    path = _audit_path()
    try:
        if path.exists():
            path.unlink()

        _log("off")
        assert not path.exists(), "off mode must not write"

        _log("meta")
        entry = json.loads(path.read_text(encoding="utf-8").strip().splitlines()[-1])
        assert "messages" not in entry, "meta mode must not log full messages"
        assert entry["prompt_eval_count"] == 7800
        assert entry["ctx_saturation"] == round(7800 / 8192, 3)
        assert entry["parser_paths"] == ["xml_tag"]
        assert entry["message_meta"][0]["role"] == "system"
        assert entry["tools_schema_count"] == 1

        _log("full")
        entry = json.loads(path.read_text(encoding="utf-8").strip().splitlines()[-1])
        assert entry["messages"] == MESSAGES, "full mode logs the message array"
        assert entry["native_tool_calls"] == 0 and entry["parsed_tool_calls"] == 1

        # Viewer handles both shapes
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        for line in lines:
            summary = summarize_exchange(json.loads(line))
            text = render_exchange(summary)
            assert "sat=0.952" in text
            assert "approaching num_ctx" in text or "SATURATED" in text
            assert "xml_tag" in text
        # Full-mode entry classifies the CURRENT STATE injection
        full_summary = summarize_exchange(json.loads(lines[-1]))
        assert full_summary["message_meta"][0]["kind"].startswith("CURRENT STATE")

        print("OK test_llm_audit: modes, token telemetry, viewer rendering")
        return 0
    finally:
        debug_log.load_active_session_id = orig_loader  # type: ignore
        if path.exists():
            path.unlink()
        try:
            path.parent.rmdir()
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
