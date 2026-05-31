"""Tests for PCAP credential draft builder."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.credential_extract import build_login_forms_draft, has_login_evidence

SAMPLE_FORMS = """
frame.number\thttp.request.uri\turlencoded-form.key\turlencoded-form.value
"818"\t"/?_type=loginData&_tag=login_entry"\t"Password,Username,_sessionTOKEN,action"\t"934a4dba,user,678517623654080274025474,login"
"""


def test_has_login_evidence():
    assert has_login_evidence({"http_forms": SAMPLE_FORMS})


def test_build_draft_includes_login_entry():
    draft = build_login_forms_draft({"http_forms": SAMPLE_FORMS})
    assert draft
    assert "login_entry" in draft
    assert "818" in draft
