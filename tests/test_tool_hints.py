"""Tests for message → tool hint parsing."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.tool_hints import parse_hash_crack_hints, parse_pcap_analysis_hints, format_crack_hash_call


def test_parse_salt_and_prefix():
    msg = (
        'Crack this sha256 "18846efb090813788c3246ce05884e7155eee92186ec23569abc0c39b44b7032" '
        'with its salt xmlObj "55077791"'
    )
    h = parse_hash_crack_hints(msg)
    assert h["target_hash"].startswith("18846")
    assert h["salt"] == "55077791"
    assert h.get("known_prefix") == "xmlObj"
    assert "salt=" in format_crack_hash_call(h)


def test_parse_complex_mask():
    msg = "crack this password which represents 1 upper, 7 lower, 2 digits, and 2 punctuation characters"
    h = parse_hash_crack_hints(msg)
    assert h.get("mask") == "ULLLLLLLNN!!"



def test_pcap_login_xml_filter():
    h = parse_pcap_analysis_hints("decode http packets with login and xmlobj")
    assert "http" in h["filter_expression"]
    assert "login" in h["filter_expression"].lower()


def test_pcap_frame_number():
    h = parse_pcap_analysis_hints("show packet 42")
    assert h["filter_expression"] == "frame.number == 42"


if __name__ == "__main__":
    test_parse_salt_and_prefix()
    test_parse_complex_mask()
    test_pcap_login_xml_filter()
    test_pcap_frame_number()
    print("All tool_hints tests passed.")
