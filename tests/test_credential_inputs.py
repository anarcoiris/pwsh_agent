"""Tests for quote-aware web auth credential extraction."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.credential_inputs import extract_web_auth_credentials, reconcile_login_args


def test_password_with_embedded_quote_and_slash():
    msg = 'login to http://192.168.1.1 with username: "user" and password: "Qtpowppu27"/"'
    creds = extract_web_auth_credentials(msg)
    assert creds["user"] == "user"
    assert creds["password"] == 'Qtpowppu27"/'


def test_multiline_quoted_password():
    msg = (
        'login into http://192.168.1.1 with username: "user" and password: "321123Aa\n    !"'
    )
    creds = extract_web_auth_credentials(msg)
    assert creds["password"] == "321123Aa\n    !"


def test_reconcile_overwrites_mangled_llm_password():
    anchor = {"user": "user", "password": 'Qtpowppu27"/'}
    llm_args = {"url": "http://192.168.1.1", "user": "user", "password": "Qtpowppu27"}
    fixed, corrected = reconcile_login_args(llm_args, anchor)
    assert corrected is True
    assert fixed["password"] == 'Qtpowppu27"/'
