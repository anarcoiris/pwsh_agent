"""Optional mission evaluator using a secondary conversational model."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx
from ollama import AsyncClient


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


class MissionEvaluator:
    def __init__(self, host: str, model: str, temperature: float = 0.1):
        self.host = host
        self.model = model
        self.temperature = temperature
        self.client = AsyncClient(host=host, timeout=httpx.Timeout(120.0))

    @staticmethod
    def should_run(prompt: str) -> bool:
        lower = (prompt or "").lower()
        return any(
            k in lower
            for k in ("retrieve", "login", "password", "xmlobj", "salt", "don't stop", "do not stop")
        )

    async def evaluate(
        self,
        prompt: str,
        recent_tools: list[str],
        recent_results: list[str],
        objective_satisfied: bool,
    ) -> dict[str, Any]:
        msg = (
            "You are evaluating mission progress. Return strict JSON only with keys: "
            "status (complete|continue|stalled), next_tool, hint, missing (array).\n"
            f"Objective: {prompt[:600]}\n"
            f"objective_satisfied={objective_satisfied}\n"
            f"recent_tools={recent_tools[-5:]}\n"
            f"recent_results={recent_results[-3:]}\n"
            "If objective_satisfied is false and recent tools are repetitive, set status=stalled."
        )
        resp = await self.client.chat(
            model=self.model,
            messages=[{"role": "user", "content": msg}],
            options={"temperature": self.temperature, "num_predict": 512},
            stream=False,
        )
        content = (resp.message.content or "").strip()
        m = _JSON_RE.search(content)
        if not m:
            return {"status": "continue", "next_tool": "", "hint": "", "missing": []}
        try:
            data = json.loads(m.group(0))
        except Exception:
            return {"status": "continue", "next_tool": "", "hint": "", "missing": []}
        if not isinstance(data, dict):
            return {"status": "continue", "next_tool": "", "hint": "", "missing": []}
        data.setdefault("status", "continue")
        data.setdefault("next_tool", "")
        data.setdefault("hint", "")
        data.setdefault("missing", [])
        return data
