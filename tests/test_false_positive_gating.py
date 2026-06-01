"""Components 2, 3, 4 — conversational/coding/review prompts get no hard tool gate."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.chat_goals import (
    ChatGoalRegistry,
    _build_credential_session_goal,
    _session_had_credential_work,
)
from core.llm_utils import DynamicContextBuilder


# ── Test 1 (Component 2) — coding/review prompts do not match any goal ──────────

def test_coding_review_prompts_match_no_goal():
    for msg in (
        "review this Python script for security issues",
        "write a Python script to connect to an API",
    ):
        assert ChatGoalRegistry.match_message(msg) is None, msg


# ── Test 2 (Component 3) — general/review prompts avoid the RECONNAISSANCE phase ─

def test_general_query_emits_general_phase():
    ctx = DynamicContextBuilder.build_context([], anchor_query="what are my options here?")
    assert "GENERAL / ANALYSIS" in ctx
    assert "RECONNAISSANCE" not in ctx


def test_review_prompt_not_forced_into_recon():
    ctx = DynamicContextBuilder.build_context(
        [], anchor_query="review this Python script for security issues"
    )
    assert "RECONNAISSANCE" not in ctx


# ── Test 3 (Component 4) — dev intent blocks the credential session goal ─────────

def _session_with_credential_work() -> list[dict]:
    return [
        {
            "role": "tool",
            "name": "find_and_grep",
            "content": json.dumps({"success": True, "matches": ["password"]}),
        },
    ]


def test_credential_session_goal_skipped_for_dev_intent():
    session = _session_with_credential_work()
    # The session genuinely had forensic work...
    assert _session_had_credential_work(session) is True
    # ...but a coding prompt must NOT fire the credential goal.
    goal = _build_credential_session_goal(
        "write a Python script to handle login validation", session
    )
    assert goal is None


def test_credential_session_goal_still_fires_for_real_followup():
    session = _session_with_credential_work()
    goal = _build_credential_session_goal("expand the search for the password salt", session)
    assert goal is not None
    assert "find_and_grep" in goal.required_tools


def test_passwd_find_mission_uses_file_find_phase():
    from core.task_intent import detect_mission_kind

    msg = (
        "find any passwd*.txt or pass*.txt file in your workspace and nearby folders, "
        "then report the content"
    )
    assert detect_mission_kind(msg) == "file_find"
    ctx = DynamicContextBuilder.build_context([], anchor_query=msg)
    assert "FILE DISCOVERY" in ctx
    assert ".pulse/pcap_logs" not in ctx.split("Do NOT")[0]


def test_file_find_salvage_uses_find_file_not_grep():
    from core.intent_salvage import redirect_misrouted_search_tool, salvage_intent_tool_call

    msg = (
        "find any passwd*.txt or pass*.txt file in your workspace and nearby folders, "
        "then report the content"
    )
    salvaged = salvage_intent_tool_call("", msg)
    assert salvaged is not None
    assert salvaged["function"]["name"] == "find_file"
    assert salvaged["function"]["arguments"]["name"] == "passwd*.txt"

    new_name, new_args, note = redirect_misrouted_search_tool(
        "find_and_grep",
        {"pattern": "passwd", "path_glob": "*.pulse/**"},
        msg,
    )
    assert new_name == "find_file"
    assert new_args["name"] == "passwd*.txt"
    assert note


def test_hash_cracking_excludes_grep_salvage():
    from core.intent_salvage import salvage_intent_tool_call
    msg = 'crack this hash "18846efb090813788c3246ce05884e7155eee92186ec23569abc0c39b44b7032" with this salt "55077791" to recover password'
    salvaged = salvage_intent_tool_call(
        raw_content="Task is complete.",
        user_context=msg,
        session_id="test_session"
    )
    assert salvaged is None

