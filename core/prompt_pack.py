"""Four-file prompt contract: AGENTS, SOUL, TOOLS, CURRENT_STATE."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.runtime_paths import app_root
from core.specialists import SPECIALIST_REGISTRY, TOOL_SUMMARIES

DEFAULT_BUDGETS = {
    "agents_tokens": 1000,
    "soul_tokens": 500,
    "tools_tokens": 1000,
    "current_state_tokens": 1500,
}


def trim_to_token_budget(text: str, max_tokens: int) -> str:
    """Trim text to approximate token budget (~4 chars/token)."""
    if max_tokens <= 0:
        return ""
    max_chars = max_tokens * 4
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    if max_chars <= 4:
        return text[:max_chars]
    return text[: max_chars - 3].rstrip() + "..."


def build_tools_md(agent_id: str) -> str:
    """Bullet list of active specialist tools with one-line when-to-use."""
    tools = sorted(SPECIALIST_REGISTRY.get(agent_id, SPECIALIST_REGISTRY["lead"]))
    lines = [f"# TOOLS — {agent_id}", ""]
    for name in tools:
        summary = TOOL_SUMMARIES.get(name, "See tool schema.")
        lines.append(f"- **{name}** — {summary}")
    lines.append("")
    lines.append("Emit ONE action tool per turn (multiple append_note allowed for LEAD).")
    return "\n".join(lines)


@dataclass
class PromptBudgets:
    agents_tokens: int = 1000
    soul_tokens: int = 500
    tools_tokens: int = 1000
    current_state_tokens: int = 1500

    @classmethod
    def from_config(cls, cfg: dict[str, Any] | None) -> "PromptBudgets":
        cfg = cfg or {}
        return cls(
            agents_tokens=int(cfg.get("agents_tokens", DEFAULT_BUDGETS["agents_tokens"])),
            soul_tokens=int(cfg.get("soul_tokens", DEFAULT_BUDGETS["soul_tokens"])),
            tools_tokens=int(cfg.get("tools_tokens", DEFAULT_BUDGETS["tools_tokens"])),
            current_state_tokens=int(
                cfg.get("current_state_tokens", DEFAULT_BUDGETS["current_state_tokens"])
            ),
        )


class PromptPack:
    """Load, trim, and assemble the 4-file prompt contract."""

    def __init__(
        self,
        *,
        state_dir: Path | None = None,
        budgets: PromptBudgets | None = None,
    ):
        self.state_dir = state_dir or (app_root() / "state")
        self.budgets = budgets or PromptBudgets()

    def load_agents_md(self) -> str:
        path = self.state_dir / "AGENTS.md"
        if path.is_file():
            return path.read_text(encoding="utf-8")
        return ""

    def load_soul_md(self) -> str:
        path = self.state_dir / "SOUL.md"
        if path.is_file():
            return path.read_text(encoding="utf-8")
        return ""

    def assemble_system(
        self,
        *,
        active_agent: str = "lead",
        network_mode: str = "SANDBOX",
        session_id: str = "",
    ) -> str:
        """Pinned system prompt: AGENTS + SOUL + TOOLS (CURRENT_STATE injected per turn)."""
        agents = trim_to_token_budget(self.load_agents_md(), self.budgets.agents_tokens)
        soul = trim_to_token_budget(self.load_soul_md(), self.budgets.soul_tokens)
        tools_md = trim_to_token_budget(
            build_tools_md(active_agent),
            self.budgets.tools_tokens,
        )

        parts = [
            "You are Pulse Windows Agent — an autonomous operator on Windows via PowerShell.",
            f"ACTIVE AGENT: {active_agent} | MODE: [{network_mode}]",
        ]
        if session_id:
            parts.append(f"Session: {session_id}")

        parts.append(
            "TOOL CALL FORMAT: use <tool_call> XML with valid JSON only.\n"
            '<tool_call>\n{"name": "tool_name", "arguments": {...}}\n</tool_call>'
        )

        if agents:
            parts.append(f"### AGENTS ###\n{agents}\n################")
        if soul:
            parts.append(f"### SOUL ###\n{soul}\n############")
        if tools_md:
            parts.append(f"### TOOLS ###\n{tools_md}\n#############")

        return "\n\n".join(parts)

    def schemas_for_agent(self, agent_id: str, max_chars: int = 2400) -> list[dict[str, Any]]:
        """Return TOOLS_SCHEMA entries for the active specialist only."""
        import tools

        names = SPECIALIST_REGISTRY.get(agent_id, SPECIALIST_REGISTRY["lead"])
        out: list[dict[str, Any]] = []
        current_len = 0
        for schema in tools.TOOLS_SCHEMA:
            name = schema.get("function", {}).get("name", "")
            if name not in names:
                continue
            serialized = json.dumps(schema, indent=2)
            if current_len + len(serialized) + 10 > max_chars:
                if not out:
                    out.append(schema)
                break
            out.append(schema)
            current_len += len(serialized) + 2
        return out
