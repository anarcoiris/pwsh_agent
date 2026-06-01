"""Phase 2 tests: capability registry + context_router de-biasing + tool wiring."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.capabilities import (
    tools_for_capabilities,
    tools_for_domain,
    capabilities_for_tool,
    get_capability,
)
from core.context_router import ContextRouter


INCIDENT = (
    'plan a way to try user: user and password: "workspace/pwd.txt" '
    "against http://192.168.1.1"
)


# ── Capability registry ──────────────────────────────────────────────────────

def test_http_auth_resolves_to_login_tool():
    assert tools_for_capabilities(["http_auth_attempt"]) == ["try_http_login"]


def test_hash_crack_capability_isolated():
    assert tools_for_capabilities(["hash_crack"]) == ["crack_hash"]


def test_tools_for_domain_web_auth():
    tools = tools_for_domain("web_auth")
    assert "try_http_login" in tools


def test_reverse_lookup_and_safety():
    assert "http_auth_attempt" in capabilities_for_tool("try_http_login")
    cap = get_capability("http_auth_attempt")
    assert cap is not None and cap.network_egress is True


def test_unknown_capability_ignored():
    assert tools_for_capabilities(["does_not_exist"]) == []


# ── Router de-biasing: the smoking gun ───────────────────────────────────────

def _derive(query: str):
    return set(ContextRouter._derive_tool_set([], None, query, "GENERAL"))


def test_password_no_longer_routes_to_hash_tools():
    tools = _derive(INCIDENT)
    assert "try_http_login" in tools
    assert "crack_hash" not in tools
    assert "hash_identify" not in tools


def test_explicit_hash_still_routes_to_crack():
    tools = _derive("crack this sha256 hash with hashpro")
    assert "crack_hash" in tools


def test_login_keyword_surfaces_web_tools():
    tools = _derive("attempt a login on http://10.0.0.5")
    assert "try_http_login" in tools


# ── Tool wiring ──────────────────────────────────────────────────────────────

def test_tool_registered_everywhere():
    import tools as t
    assert "try_http_login" in t.__all__
    assert callable(t.try_http_login)
    names = {s["function"]["name"] for s in t.TOOLS_SCHEMA}
    assert "try_http_login" in names


print("All capabilities/routing tests passed.")
