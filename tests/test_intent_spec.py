"""Tests for the Phase 1 intent formalization layer (fallback + parsing/merge)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.intent_spec import (
    DOMAINS,
    IntentSpec,
    SafetyAssessment,
    build_fallback_spec,
    merge_specs,
)


# ── The triggering incident: must NOT be classified as a hash task ───────────

INCIDENT = (
    'plan a way to try user: user and password: "workspace/pwd.txt" '
    "(the content of the file). The site is http://192.168.1.1"
)


def test_incident_is_web_auth_not_hash():
    spec = build_fallback_spec(INCIDENT)
    assert spec.domain == "web_auth", spec.domain
    assert spec.domain != "hash"
    assert "http_auth_attempt" in spec.capabilities
    assert "hash_crack" not in spec.capabilities


def test_incident_extracts_targets_and_egress():
    spec = build_fallback_spec(INCIDENT)
    assert "http://192.168.1.1" in spec.targets
    # password source file is captured as input and as a file to read
    assert spec.inputs.get("password_source") == "workspace/pwd.txt"
    assert spec.safety.network_egress is True
    assert spec.safety.needs_confirmation is True


# ── Domain detection across capability areas ─────────────────────────────────

def test_explicit_hash_still_hash():
    spec = build_fallback_spec("crack this sha256 hash with hashpro")
    assert spec.domain == "hash"
    assert "hash_crack" in spec.capabilities


def test_code_review_domain():
    spec = build_fallback_spec("review this Python script for security issues in app.py")
    assert spec.domain == "code_review"
    assert "code_review" in spec.capabilities


def test_scripting_domain():
    spec = build_fallback_spec("write a PowerShell script to back up my documents folder")
    assert spec.domain == "scripting"
    assert "scripting" in spec.capabilities


def test_scheduled_task_is_sysadmin():
    spec = build_fallback_spec("create a scheduled task that runs cleanup.ps1 every night")
    assert spec.domain == "sysadmin"
    assert "task_schedule" in spec.capabilities
    assert spec.safety.system_modification is True


def test_conversation_question():
    spec = build_fallback_spec("what is the difference between TLS 1.2 and 1.3?")
    assert spec.domain == "conversation"


def test_pcap_domain():
    spec = build_fallback_spec("analyze last_capture.pcapng for login packets")
    assert spec.domain == "pcap"


def test_all_domains_are_valid():
    for msg in (INCIDENT, "crack a hash", "review code in x.py", "hello"):
        spec = build_fallback_spec(msg)
        assert spec.domain in DOMAINS


# ── Serialization round-trip ─────────────────────────────────────────────────

def test_roundtrip_to_from_dict():
    spec = build_fallback_spec(INCIDENT)
    restored = IntentSpec.from_dict(spec.to_dict())
    assert restored.domain == spec.domain
    assert restored.targets == spec.targets
    assert restored.safety.network_egress == spec.safety.network_egress
    assert isinstance(restored.safety, SafetyAssessment)


def test_from_dict_coerces_bad_types():
    spec = IntentSpec.from_dict({
        "domain": "TOTALLY-UNKNOWN",
        "objectives": "a single string",
        "confidence": "1.5",
        "capabilities": None,
        "safety": None,
    })
    assert spec.domain == "mixed"  # unknown but non-empty → mixed
    assert spec.objectives == ["a single string"]
    assert spec.confidence == 1.0  # clamped
    assert spec.capabilities == []


# ── Merge: LLM wins, fallback fills gaps, safety is a union ──────────────────

def test_merge_llm_over_fallback():
    fallback = build_fallback_spec(INCIDENT)
    llm = IntentSpec.from_dict({
        "summary": "Attempt HTTP login to 192.168.1.1",
        "domain": "web_auth",
        "objectives": ["read file", "attempt login", "report"],
        "success_criteria": ["definitive auth result"],
        "safety": {"network_egress": False, "destructive": True},
        "source": "llm",
    })
    merged = merge_specs(fallback, llm)
    assert merged.objectives == ["read file", "attempt login", "report"]
    assert merged.success_criteria == ["definitive auth result"]
    # fallback fills targets (llm omitted them)
    assert "http://192.168.1.1" in merged.targets
    # safety is a union — fallback egress=True is preserved despite llm False
    assert merged.safety.network_egress is True
    assert merged.safety.destructive is True
    assert merged.source == "llm+fallback"


def test_merge_keeps_fallback_domain_when_llm_generic():
    fallback = build_fallback_spec("crack this sha256 hash with hashpro")
    llm = IntentSpec.from_dict({"domain": "general", "source": "llm"})
    merged = merge_specs(fallback, llm)
    assert merged.domain == "hash"


print("All intent_spec tests passed.")
