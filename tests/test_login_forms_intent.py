"""Deliverable extraction for login_forms.txt credential workflows."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.task_intent import TaskIntentExtractor

USER_MSG = (
    "find and read your latest reports and plans? You found some 'user' and 'password' values - "
    "only missing is the XML packet with the xmlObj (salt) - Find and save all these values in a "
    "file named 'login_forms.txt'. Once done,use hashpro to crack the sha256."
)


def test_extract_login_forms_deliverable():
    intent = TaskIntentExtractor.parse(USER_MSG)
    assert any("login_forms.txt" in d for d in intent.deliverables)


def test_hash_goal_prefers_login_forms():
    from core.chat_goals import ChatGoalRegistry

    goal = ChatGoalRegistry.match_message(USER_MSG)
    assert goal is not None
    assert goal.hints.get("deliverable_path", "").endswith("login_forms.txt") or any(
        "login_forms" in str(goal.hints.get("context_directive", ""))
        for _ in [1]
    )
