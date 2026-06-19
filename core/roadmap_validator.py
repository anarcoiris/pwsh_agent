"""Coder-backed validation of VibeThinker roadmap JSON (cooperative workflow)."""

from __future__ import annotations

import json
import re
from typing import Any

from core.model_dispatch import TurnPhase, chat_options_for_phase, endpoint_for_phase, model_for_phase

_VALID_AGENTS = frozenset({"lead", "workspace", "web", "recon", "forensic", "crypto"})

_VALIDATE_SYSTEM = (
    "You are a code-agent validator. You receive an IntentSpec and a roadmap JSON array "
    "produced by a planner model. Verify structure and feasibility.\n\n"
    "Check:\n"
    "- Valid JSON array of step objects\n"
    "- Each step has: id, label, tool_hint, assigned_agent, success_criteria\n"
    "- assigned_agent is one of: lead, workspace, web, recon, forensic, crypto\n"
    "- tool_hint names a plausible tool for the domain\n"
    "- depends_on references existing step ids (if present)\n"
    "- No duplicate step ids\n\n"
    'Output JSON ONLY: {"status":"valid"|"corrected"|"reject", '
    '"issues":["..."], "corrected_roadmap":[...] or null, "reason":"..."}'
)

_VALIDATE_USER_TPL = (
    "IntentSpec summary: {summary}\n"
    "Domain: {domain}\n"
    "Deliverables: {deliverables}\n\n"
    "Roadmap to validate:\n{roadmap_json}\n"
)


def _parse_validation_response(content: str) -> dict[str, Any]:
    m = re.search(r"\{.*\}", content or "", re.DOTALL)
    if not m:
        return {"status": "reject", "issues": ["empty validator response"], "reason": "no JSON"}
    try:
        data = json.loads(m.group(0))
    except (json.JSONDecodeError, TypeError):
        return {"status": "reject", "issues": ["invalid JSON from validator"], "reason": "parse error"}
    if not isinstance(data, dict):
        return {"status": "reject", "issues": ["validator returned non-object"], "reason": "bad shape"}
    status = str(data.get("status", "reject")).lower()
    if status not in ("valid", "corrected", "reject"):
        status = "reject"
    data["status"] = status
    return data


def deterministic_roadmap_checks(steps: list[dict]) -> list[str]:
    """Fast structural checks without LLM."""
    issues: list[str] = []
    if not steps:
        issues.append("roadmap is empty")
        return issues
    if not isinstance(steps, list):
        issues.append("roadmap is not a list")
        return issues

    seen_ids: set[str] = set()
    required = ("id", "label", "tool_hint", "assigned_agent", "success_criteria")
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            issues.append(f"step {i} is not an object")
            continue
        for field in required:
            if not str(step.get(field, "")).strip():
                issues.append(f"step {i} missing {field}")
        sid = str(step.get("id", "")).strip()
        if sid:
            if sid in seen_ids:
                issues.append(f"duplicate step id: {sid}")
            seen_ids.add(sid)
        agent = str(step.get("assigned_agent", "")).strip().lower()
        if agent and agent not in _VALID_AGENTS:
            issues.append(f"step {sid or i} invalid assigned_agent: {agent}")

    for step in steps:
        if not isinstance(step, dict):
            continue
        sid = str(step.get("id", "")).strip()
        for dep in step.get("depends_on") or []:
            dep_s = str(dep).strip()
            if dep_s and dep_s not in seen_ids:
                issues.append(f"step {sid} depends_on unknown id: {dep_s}")
    return issues


class RoadmapValidator:
    """Uses qwen-coder on the coder endpoint to validate VT roadmaps."""

    def __init__(self, host: str, model: str, agent_config: dict[str, Any]):
        import httpx
        from ollama import AsyncClient

        self.host = host
        self.model = model
        self.agent_config = agent_config
        self.client = AsyncClient(host=host, timeout=httpx.Timeout(90.0))

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> "RoadmapValidator | None":
        model, _ = model_for_phase(TurnPhase.VALIDATE, cfg)
        if not model:
            return None
        host = endpoint_for_phase(TurnPhase.VALIDATE, cfg)
        return cls(host=host, model=model, agent_config=cfg)

    async def validate(self, intent_spec: Any, steps: list[dict]) -> dict[str, Any]:
        """Validate roadmap; never raises."""
        det_issues = deterministic_roadmap_checks(steps)
        if det_issues and not steps:
            return {
                "status": "reject",
                "issues": det_issues,
                "corrected_roadmap": None,
                "reason": "; ".join(det_issues[:3]),
            }

        roadmap_json = json.dumps(steps, ensure_ascii=False)[:6000]
        user_content = _VALIDATE_USER_TPL.format(
            summary=getattr(intent_spec, "summary", str(intent_spec))[:400],
            domain=getattr(intent_spec, "domain", "general"),
            deliverables="; ".join(getattr(intent_spec, "deliverables", []))[:200] or "(none)",
            roadmap_json=roadmap_json,
        )
        if det_issues:
            user_content += "\n\nDeterministic pre-check issues:\n" + "\n".join(f"- {x}" for x in det_issues)

        messages = [
            {"role": "system", "content": _VALIDATE_SYSTEM},
            {"role": "user", "content": user_content},
        ]
        try:
            opts = chat_options_for_phase(TurnPhase.VALIDATE, self.agent_config)
            resp = await self.client.chat(
                model=self.model,
                messages=messages,
                options=opts,
                format="json",
                stream=False,
            )
            parsed = _parse_validation_response((resp.message.content or "").strip())
        except Exception as exc:
            if det_issues:
                return {
                    "status": "reject",
                    "issues": det_issues,
                    "corrected_roadmap": None,
                    "reason": f"validator error: {exc}",
                }
            return {"status": "valid", "issues": [], "corrected_roadmap": None, "reason": ""}

        if parsed.get("status") == "corrected":
            corrected = parsed.get("corrected_roadmap")
            if isinstance(corrected, list) and corrected:
                still_bad = deterministic_roadmap_checks(corrected)
                if not still_bad:
                    parsed["corrected_roadmap"] = corrected
                else:
                    parsed["status"] = "reject"
                    parsed["issues"] = list(parsed.get("issues") or []) + still_bad
        elif parsed.get("status") == "valid" and det_issues:
            parsed["issues"] = list(parsed.get("issues") or []) + det_issues

        return parsed


async def validate_roadmap(
    intent_spec: Any,
    steps: list[dict],
    cfg: dict[str, Any],
) -> tuple[list[dict], str]:
    """Validate steps; return (final_steps, rejection_reason).

    rejection_reason is non-empty when validation failed entirely.
    """
    planner_cfg = cfg.get("planner", {})
    if not bool(planner_cfg.get("validate_enabled", True)):
        return steps, ""

    validator = RoadmapValidator.from_config(cfg)
    if validator is None:
        issues = deterministic_roadmap_checks(steps)
        if issues:
            return [], "; ".join(issues[:5])
        return steps, ""

    result = await validator.validate(intent_spec, steps)
    status = result.get("status", "reject")
    if status == "valid":
        return steps, ""
    if status == "corrected":
        corrected = result.get("corrected_roadmap")
        if isinstance(corrected, list) and corrected:
            return corrected, ""
    issues = result.get("issues") or []
    reason = str(result.get("reason", "")) or "; ".join(str(x) for x in issues[:5])
    return [], reason
