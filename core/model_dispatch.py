"""
core/model_dispatch.py — Centralised model dispatch for the 3-phase pipeline.

Pipeline:
  INTAKE   → chat-analyzer (7B, 8k ctx)   : user msg → structured IntentSpec
  PLAN     → vibethinker  (3B, 16k ctx)   : monologue + roadmap decomposition
  EXECUTE  → qwen-coder   (7B, 8k ctx)    : tool_calls, code, strict syntax
  EVALUATE → vibethinker  (3B, 16k ctx)   : exec result → next step / done?

Models are loaded/unloaded sequentially — never simultaneously.
When Pascal GPUs are added, pin each model to its own GPU and drop unload.
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass


class TurnPhase(str, Enum):
    """Which phase of the pipeline is currently executing."""
    INTAKE   = "intake"    # chat-analyzer: user msg → IntentSpec
    PLAN     = "plan"      # vibethinker:   IntentSpec + monologue → roadmap
    EXECUTE  = "execute"   # qwen-coder:    tool_calls, code, syntax
    EVALUATE = "evaluate"  # vibethinker:   exec result → next step / done?


def model_for_phase(phase: TurnPhase, cfg: dict[str, Any]) -> tuple[str, int]:
    """Return (model_name, num_ctx) for the given pipeline phase.

    Args:
        phase:  The pipeline phase (TurnPhase enum).
        cfg:    The loaded config dict (top-level, not the ollama sub-key).

    Returns:
        (model_name, num_ctx) — always returns something usable.
    """
    ollama = cfg.get("ollama", {})
    default_model   = ollama.get("default_model",       "qwen2.5-coder:7b-instruct")
    intake_model    = ollama.get("conversational_model", "chat-analyzer")
    planner_model   = ollama.get("planner_model",        "vibethinker:3b")
    num_ctx         = int(ollama.get("num_ctx",          8192))
    num_ctx_planner = int(ollama.get("num_ctx_planner",  16384))

    if phase == TurnPhase.INTAKE:
        return intake_model, num_ctx
    if phase in (TurnPhase.PLAN, TurnPhase.EVALUATE):
        return planner_model, num_ctx_planner
    # EXECUTE (default)
    return default_model, num_ctx


def num_predict_for_phase(phase: TurnPhase, cfg: dict[str, Any]) -> int:
    """Return num_predict tokens for the given phase."""
    ollama = cfg.get("ollama", {})
    if phase in (TurnPhase.PLAN, TurnPhase.EVALUATE):
        return int(ollama.get("num_predict_planner", 2048))
    return int(ollama.get("num_predict", 3072))


def temperature_for_phase(phase: TurnPhase, cfg: dict[str, Any]) -> float:
    """Return temperature for the given phase."""
    if phase == TurnPhase.INTAKE:
        return float(cfg.get("intent", {}).get("temperature", 0.1))
    if phase in (TurnPhase.PLAN, TurnPhase.EVALUATE):
        return float(cfg.get("planner", {}).get("temperature", 0.4))
    return 0.1  # EXECUTE: deterministic tool-calling


def should_reevaluate(trigger: str, cfg: dict[str, Any]) -> bool:
    """Return True when the planner (VT) should re-evaluate after this trigger.

    Trigger names (from config.yaml planner.reevaluate_on):
        "start"       — beginning of a new mission
        "failure"     — any step FAILED or BLOCKED
        "warning"     — any system warning / stall detected
        "exec_result" — after any code / script / command execution
    """
    planner = cfg.get("planner", {})
    triggers: list[str] = planner.get("reevaluate_on", ["start", "failure", "warning", "exec_result"])
    return trigger in triggers


# ──────────────────────────────────────────────────────────────────────────────
# Monologue prompt helpers
# ──────────────────────────────────────────────────────────────────────────────

_MONOLOGUE_SYSTEM = (
    "You are VibeThinker, a strategic reasoning model leading an AI agent. "
    "You think out loud in the FIRST PERSON. Write as if you are thinking privately "
    "before committing to a plan. Be specific, critical, and structured. "
    "Do NOT produce tool calls — this is your internal reasoning phase. "
    "Speak naturally, as if you are a focused engineer talking to yourself."
)

_MONOLOGUE_USER_TPL = (
    "Here is what the user wants, formalised by the intake model:\n\n"
    "{intent_summary}\n\n"
    "Domain: {domain}\n"
    "Objectives: {objectives}\n"
    "Targets: {targets}\n"
    "Deliverables: {deliverables}\n"
    "Safety flags: {safety}\n\n"
    "Think through this request. What is the real goal? What are the risks? "
    "What is the best sequence of actions? What could go wrong? "
    "Be honest about what you don't know."
)

_ROADMAP_SYSTEM = (
    "You are VibeThinker, a strategic planning model for an AI agent. "
    "After your reasoning, produce a STRUCTURED ROADMAP as a JSON array of steps. "
    "Each step: {id, label, tool_hint, assigned_agent, success_criteria, rationale}. "
    "assigned_agent must be one of: lead, workspace, web. "
    "tool_hint is the primary tool name (e.g. write_file, run_script, http_get). "
    "Output JSON ONLY — a valid array, no prose, no markdown."
)

_ROADMAP_USER_TPL = (
    "Your internal reasoning:\n{monologue}\n\n"
    "Now produce the roadmap JSON array for this mission:\n{intent_summary}\n"
    "Domain: {domain} | Objectives: {objectives}"
)


def build_monologue_messages(
    intent_spec: "Any",
    *,
    prior_monologue: str = "",
    exec_result: str = "",
) -> list[dict[str, str]]:
    """Build the messages list for a VibeThinker monologue call.

    Args:
        intent_spec:    The current IntentSpec object.
        prior_monologue: Previous monologue text (for re-evaluation turns).
        exec_result:    The execution result that triggered re-evaluation.
    """
    user_content = _MONOLOGUE_USER_TPL.format(
        intent_summary=getattr(intent_spec, "summary", str(intent_spec))[:600],
        domain=getattr(intent_spec, "domain", "general"),
        objectives="; ".join(getattr(intent_spec, "objectives", []))[:300] or "(none yet)",
        targets="; ".join(getattr(intent_spec, "targets", []))[:200] or "(none)",
        deliverables="; ".join(getattr(intent_spec, "deliverables", []))[:200] or "(none)",
        safety=str(getattr(intent_spec, "safety", ""))[:120],
    )
    if exec_result:
        user_content += f"\n\nLatest execution result:\n{exec_result[:600]}"
    if prior_monologue:
        user_content += f"\n\nYour previous reasoning:\n{prior_monologue[:400]}"

    return [
        {"role": "system", "content": _MONOLOGUE_SYSTEM},
        {"role": "user",   "content": user_content},
    ]


def build_roadmap_messages(
    intent_spec: "Any",
    monologue: str,
) -> list[dict[str, str]]:
    """Build messages for the VibeThinker roadmap decomposition call."""
    return [
        {"role": "system", "content": _ROADMAP_SYSTEM},
        {"role": "user",   "content": _ROADMAP_USER_TPL.format(
            monologue=monologue[:800],
            intent_summary=getattr(intent_spec, "summary", str(intent_spec))[:400],
            domain=getattr(intent_spec, "domain", "general"),
            objectives="; ".join(getattr(intent_spec, "objectives", []))[:300] or "(none)",
        )},
    ]


def build_evaluation_messages(
    intent_spec: "Any",
    monologue: str,
    exec_result: str,
    roadmap_status: str,
) -> list[dict[str, str]]:
    """Build messages for a VibeThinker evaluation call after execution.

    The evaluation asks VT: given what happened, what is the next step?
    Output: JSON {status: "continue"|"done"|"blocked"|"needs_user", next_step_id, hint, monologue}
    """
    system = (
        "You are VibeThinker, evaluating the progress of an ongoing mission. "
        "Think in FIRST PERSON. Assess what happened, decide the next step. "
        'Output JSON: {"status": "continue"|"done"|"blocked"|"needs_user", '
        '"next_step_id": "<step_id or null>", "hint": "<one line hint>", '
        '"monologue": "<your private first-person reasoning>"}'
    )
    user = (
        f"Mission goal: {getattr(intent_spec, 'summary', '')[:300]}\n\n"
        f"Current roadmap:\n{roadmap_status}\n\n"
        f"Execution result:\n{exec_result[:600]}\n\n"
        f"Your previous reasoning:\n{monologue[:400]}\n\n"
        "Evaluate: is the mission progressing? What should happen next?"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user",   "content": user},
    ]
