"""Tests for multi-endpoint model dispatch."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.model_dispatch import (
    TurnPhase,
    endpoint_for_phase,
    endpoints_config,
    model_for_phase,
    unload_after_call,
)


def test_endpoints_from_config():
    cfg = {
        "ollama": {
            "base_url": "http://localhost:11435",
            "endpoints": {
                "intake": "http://localhost:11436",
                "planner": "http://localhost:11434",
                "coder": "http://localhost:11435",
            },
            "unload_after_call": False,
        }
    }
    eps = endpoints_config(cfg)
    assert eps["intake"] == "http://localhost:11436"
    assert eps["planner"] == "http://localhost:11434"
    assert endpoint_for_phase(TurnPhase.PLAN, cfg) == "http://localhost:11434"
    assert endpoint_for_phase(TurnPhase.EXECUTE, cfg) == "http://localhost:11435"
    assert endpoint_for_phase(TurnPhase.VALIDATE, cfg) == "http://localhost:11435"
    assert unload_after_call(cfg) is False


def test_validate_phase_uses_coder_model():
    cfg = {
        "ollama": {
            "default_model": "qwen2.5-coder:7b-instruct",
            "conversational_model": "chat-analyzer",
            "planner_model": "vibethinker:3b",
        }
    }
    model, _ = model_for_phase(TurnPhase.VALIDATE, cfg)
    assert model == "qwen2.5-coder:7b-instruct"
