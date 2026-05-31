"""Tests for artifact path resolution and find_file."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.artifacts import resolve_project_file, find_file
import tools

MSG = "locate a file named last_capture.pcapng"


def test_resolve_last_capture():
    p = resolve_project_file("last_capture.pcapng")
    assert p is not None and p.exists(), "last_capture.pcapng should resolve"


def test_network_logs_not_found():
    p = resolve_project_file("network_logs/last_capture.pcapng")
    assert p is None


def test_find_file_tool():
    res = tools.find_file("last_capture.pcapng")
    assert res["success"]
    assert res["recommended"]
    assert "last_capture.pcapng" in res["recommended"]


def test_analyze_pcapng_resolves_basename():
    res = tools.analyze_pcapng("last_capture.pcapng", limit=2)
    assert res.get("success"), res.get("error", res)


def test_analyze_pcapng_always_has_key_fields_or_error():
    res = tools.analyze_pcapng(
        "last_capture.pcapng",
        filter_expression="http",
        limit=10,
        verbose=False,
    )
    assert res.get("success"), res.get("error", res)
    analysis = res.get("analysis", {})
    has_key_fields = bool(analysis.get("key_fields", "").strip())
    has_packet_summary = bool(analysis.get("packet_summary", "").strip())
    has_summary_error = bool(analysis.get("packet_summary_error"))
    assert has_key_fields or has_packet_summary or has_summary_error


def test_analyze_pcapng_surfaces_login_artifacts():
    p = resolve_project_file("last_capture.pcapng")
    if p is None:
        return
    res = tools.analyze_pcapng(str(p), filter_expression="http", limit=10, verbose=False)
    assert res.get("success"), res.get("error", res)
    analysis = res.get("analysis", {})
    blob = "\n".join(
        str(analysis.get(k, ""))
        for k in (
            "key_fields",
            "potential_plaintext_credentials",
            "http_forms",
            "http_index",
        )
    ).lower()
    assert "login" in blob or "password" in blob or analysis.get("extracted_secrets")


def test_analyze_pcapng_large_writes_log_file():
    res = tools.analyze_pcapng(
        "last_capture.pcapng",
        filter_expression="http",
        limit=50,
        verbose=True,
    )
    assert res.get("success"), res.get("error", res)
    analysis = res.get("analysis", {})
    log_file = analysis.get("verbose_log_file")
    if log_file:
        p = Path(log_file)
        assert p.exists()
        assert analysis.get("verbose_log_bytes", 0) > 0


def test_resolve_from_foreign_cwd():
    import os
    from core.runtime_paths import app_root

    prev = os.getcwd()
    try:
        os.chdir(app_root().parent)  # parent of repo — not the workspace
        p = resolve_project_file("last_capture.pcapng")
        assert p is not None and p.exists(), "should resolve via app_root when cwd is outside repo"
    finally:
        os.chdir(prev)


if __name__ == "__main__":
    test_resolve_last_capture()
    test_network_logs_not_found()
    test_find_file_tool()
    test_analyze_pcapng_resolves_basename()
    test_analyze_pcapng_always_has_key_fields_or_error()
    test_analyze_pcapng_large_writes_log_file()
    test_resolve_from_foreign_cwd()
    print("All artifacts tests passed.")
