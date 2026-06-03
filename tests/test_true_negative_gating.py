"""Component 4 — genuine PCAP/credential/hash prompts still get their hard gate."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.chat_goals import ChatGoalRegistry


def test_pcap_extraction_still_gated():
    goal = ChatGoalRegistry.match_message("extract the password from last_capture.pcapng")
    assert goal is not None
    assert "analyze_pcapng" in goal.required_tools


def test_hash_crack_still_gated():
    goal = ChatGoalRegistry.match_message("crack this target hash")
    assert goal is not None
    assert "crack_hash" in goal.required_tools


def test_explicit_crack_hash_tool_still_gated():
    goal = ChatGoalRegistry.match_message("use crack_hash on the sha256 digest")
    assert goal is not None
    assert "crack_hash" in goal.required_tools


def test_router_login_xml_packet_not_pcap_gated():
    """Router login + XML packet wording must not trigger PCAP hard gate."""
    msg = (
        "Think of a step-by-step plan to try and login into http://192.168.1.1 "
        'with username: "user" and password: "321123Aa!" using powershell commands. '
        "fetch the HTML. The router will send an XML packet after GET / then http_form."
    )
    goal = ChatGoalRegistry.match_message(msg, intent_domain="web_auth")
    assert goal is None
