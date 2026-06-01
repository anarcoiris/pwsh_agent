"""Tests for hash/salt pairing from PCAP analysis."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.credential_extract import (
    extract_hash_salt_pairs,
    find_xml_salts,
    parse_login_hashes,
    pair_hashes_with_salts,
    build_cracked_deliverable,
)
from core.task_plan import _looks_like_extraction_draft

HTTP_FORMS = (
    '"818"\t"/?_type=loginData&_tag=login_entry"\t'
    '"Password,Username,_sessionTOKEN,action"\t'
    '"934a4dba4efb69292faa401d67d0e0c7b59a42bda46058eef316d74f728420da,user,678517623654080274025474,login"\n'
    '"910"\t"/?_type=loginData&_tag=login_entry"\t'
    '"Password,Username,_sessionTOKEN,action"\t'
    '"efd76159169ae3ac66e4a37d468ef9ac978d6895e35fbd9c2913c82fe6ac9b1b,user,783244708375784786029077,login"'
)

SALT_BLOB = (
    '<ajax_response_xml_root>54252402</ajax_response_xml_root>\n'
    '<ajax_response_xml_root>36736279</ajax_response_xml_root>'
)


def test_parse_login_hashes():
    hashes = parse_login_hashes(HTTP_FORMS)
    assert len(hashes) == 2
    assert hashes[0]["hash"].startswith("934a4dba")


def test_find_xml_salts():
    salts = find_xml_salts(SALT_BLOB)
    assert salts == ["54252402", "36736279"]


def test_pair_hashes_with_salts():
    hashes = parse_login_hashes(HTTP_FORMS)
    pairs = pair_hashes_with_salts(hashes, find_xml_salts(SALT_BLOB))
    assert pairs[0]["salt"] == "54252402"
    assert pairs[1]["salt"] == "36736279"


def test_extract_hash_salt_pairs_from_analysis():
    analysis = {"http_forms": HTTP_FORMS, "key_fields": SALT_BLOB}
    pairs = extract_hash_salt_pairs(analysis)
    assert len(pairs) == 2
    assert all(p.get("salt") for p in pairs)


def test_extraction_draft_detection():
    draft = "# HTTP login forms (from analyze_pcapng http_forms)\nhash=abc"
    assert _looks_like_extraction_draft(draft)
    final = "hash=abc\nsalt=123\nplaintext=hunter2"
    assert not _looks_like_extraction_draft(final)


def test_build_cracked_deliverable():
    pairs = [{"hash": "abc", "salt": "99", "username": "user"}]
    results = [{"success": True, "password": "hunter2"}]
    text = build_cracked_deliverable(pairs, results)
    assert "plaintext=hunter2" in text
    assert "salt=99" in text


def test_build_cracked_deliverable_exhausted():
    pairs = [{"hash": "abc", "salt": "99", "username": "user"}]
    results = [{"success": False, "status": "exhausted", "error": "not found"}]
    text = build_cracked_deliverable(pairs, results)
    assert "hash=abc" in text
    assert "salt=99" in text
    assert "NOT CRACKED" in text
    assert "exhausted" in text
