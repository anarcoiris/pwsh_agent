"""One-shot vertical pipeline smoke: INTAKE -> PLAN -> VALIDATE."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.intent_spec import IntentFormalizer, IntentPlanner
from core.model_dispatch import TurnPhase, endpoint_for_phase, model_for_phase
from core.roadmap_validator import RoadmapValidator

PROMPT = "In HelloGame/ create PLAN.md and game.py - a minimal Python ASCII game."


async def main() -> None:
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    intake_url = endpoint_for_phase(TurnPhase.INTAKE, cfg)
    planner_url = endpoint_for_phase(TurnPhase.PLAN, cfg)
    intake_model, _ = model_for_phase(TurnPhase.INTAKE, cfg)
    planner_model, planner_ctx = model_for_phase(TurnPhase.PLAN, cfg)

    print("=== VERTICAL SMOKE (idle GPUs) ===")
    print(f"INTAKE  {intake_model} @ {intake_url}")
    print(f"PLAN    {planner_model} @ {planner_url} ctx={planner_ctx}")

    formalizer = IntentFormalizer(intake_url, intake_model, agent_config=cfg)
    spec = await formalizer.formalize(PROMPT)
    print(f"INTAKE  source={spec.source} domain={spec.domain} objectives={len(spec.objectives)}")

    planner = IntentPlanner(planner_url, planner_model, num_ctx=planner_ctx, agent_config=cfg)
    mono = await planner.monologue(spec)
    print(f"PLAN    monologue={len(mono)} chars")
    steps = await planner.decompose(spec, mono)
    print(f"PLAN    steps={len(steps)}")
    if steps:
        s0 = steps[0]
        print(f"        first: id={s0.get('id')} tool={s0.get('tool_hint')} agent={s0.get('assigned_agent')}")

    validator = RoadmapValidator.from_config(cfg)
    if validator:
        val = await validator.validate(spec, steps)
        print(f"VALIDATE status={val.get('status')} issues={len(val.get('issues') or [])}")
    print("CHAIN OK")


if __name__ == "__main__":
    asyncio.run(main())
