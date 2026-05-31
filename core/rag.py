"""
core/rag.py — Local lightweight Jaccard word-overlap RAG engine.

Scans knowledge/ recursively (including knowledge/tools/ playbooks),
parses YAML frontmatter for tool/phase tags, and retrieves relevant sections.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from core.runtime_paths import app_root

_KNOWLEDGE_DIR = app_root() / "knowledge"

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Return (metadata dict, body without frontmatter)."""
    m = _FRONTMATTER_RE.match(content)
    if not m:
        return {}, content
    meta: dict[str, Any] = {}
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, val = line.split(":", 1)
        key = key.strip()
        val = val.strip()
        if val.startswith("[") and val.endswith("]"):
            items = [x.strip().strip("'\"") for x in val[1:-1].split(",") if x.strip()]
            meta[key] = items
        else:
            meta[key] = val.strip("'\"")
    return meta, content[m.end():]


class LocalRAG:
    """
    In-process micro RAG system using section-level word-overlap retrieval.
    Requires no external packages or database engines.
    """

    def __init__(self, knowledge_dir: Path = _KNOWLEDGE_DIR):
        self.knowledge_dir = knowledge_dir
        self.sections: list[dict[str, Any]] = []
        self._load_knowledge_base()

    def reload(self) -> None:
        self.sections.clear()
        self._load_knowledge_base()

    def _tokenize(self, text: str) -> set[str]:
        words = re.findall(r"\b[a-zA-Z0-9_-]+\b", text.lower())
        return set(words)

    def _load_knowledge_base(self) -> None:
        if not self.knowledge_dir.exists():
            return

        for path in sorted(self.knowledge_dir.rglob("*.md")):
            try:
                raw = path.read_text(encoding="utf-8")
                meta, content = _parse_frontmatter(raw)
                file_tools = meta.get("tools", [])
                if isinstance(file_tools, str):
                    file_tools = [file_tools]
                file_phase = meta.get("phase", [])
                if isinstance(file_phase, str):
                    file_phase = [file_phase]

                parts = re.split(r"(?=(?:^|\n)#+\s+)", content)
                doc_title = path.stem.replace("_", " ").title()
                rel = path.relative_to(self.knowledge_dir)
                rel_norm = str(rel).replace("\\", "/")

                for part in parts:
                    part = part.strip()
                    if not part:
                        continue
                    title_match = re.match(r"^#+\s+(.+)", part)
                    sec_title = title_match.group(1).strip() if title_match else "General Reference"
                    section_slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", sec_title.lower()).strip("-") or "section"
                    paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", part) if p.strip()]
                    if not paragraphs:
                        paragraphs = [part]
                    for idx, para in enumerate(paragraphs, start=1):
                        chunk = para
                        tokens = self._tokenize(chunk)
                        anchor = f"{rel_norm}#{section_slug}-{idx}"
                        self.sections.append({
                            "file": rel_norm,
                            "doc_title": doc_title,
                            "section_title": sec_title,
                            "anchor": anchor,
                            "paragraph_index": idx,
                            "content": chunk,
                            "tokens": tokens,
                            "tools": list(file_tools),
                            "phase": list(file_phase),
                        })
            except Exception:
                pass

    def _score_sections(
        self,
        query: str,
        tool_names: list[str] | None = None,
        phase: str | None = None,
    ) -> list[tuple[float, dict[str, Any]]]:
        query_tokens = self._tokenize(query)
        if not query_tokens and not tool_names and not phase:
            return []

        tool_set = {t.lower() for t in (tool_names or [])}
        scored: list[tuple[float, dict[str, Any]]] = []

        for sec in self.sections:
            intersection = query_tokens.intersection(sec["tokens"])
            union = query_tokens.union(sec["tokens"])
            score = len(intersection) / len(union) if union and query_tokens else 0.0

            title_tokens = self._tokenize(f"{sec['doc_title']} {sec['section_title']}")
            title_intersection = query_tokens.intersection(title_tokens)
            if title_intersection:
                score += 0.1 * len(title_intersection)

            sec_tools = {t.lower() for t in sec.get("tools", [])}
            if tool_set and sec_tools.intersection(tool_set):
                score += 0.25 * len(sec_tools.intersection(tool_set))

            if phase and phase.lower() in [p.lower() for p in sec.get("phase", [])]:
                score += 0.2

            if tool_set and not sec_tools.intersection(tool_set) and not query_tokens:
                continue

            if score > 0 or (tool_set and sec_tools.intersection(tool_set)):
                if score <= 0 and tool_set and sec_tools.intersection(tool_set):
                    score = 0.15
                scored.append((score, sec))

        scored.sort(key=lambda x: x[0], reverse=True)
        return scored

    def _format_results(
        self,
        scored: list[tuple[float, dict[str, Any]]],
        max_chars: int,
    ) -> str:
        result_parts: list[str] = []
        total_len = 0
        for _score, sec in scored:
            formatted = (
                f"--- REFERENCE: {sec['doc_title']} -> {sec['section_title']} ({sec.get('anchor', sec.get('file', ''))}) ---\n"
                f"{sec['content']}\n"
            )
            if total_len + len(formatted) > max_chars:
                if not result_parts:
                    result_parts.append(formatted[:max_chars])
                break
            result_parts.append(formatted)
            total_len += len(formatted)
        return "\n".join(result_parts).strip()

    def retrieve(self, query: str, max_chars: int = 2500) -> str:
        scored = self._score_sections(query)
        return self._format_results(scored, max_chars)

    def retrieve_for_tools(
        self,
        tool_names: list[str],
        query: str = "",
        max_chars: int = 1500,
    ) -> str:
        scored = self._score_sections(query, tool_names=tool_names)
        if not scored and tool_names:
            scored = [
                (0.1, sec)
                for sec in self.sections
                if {t.lower() for t in sec.get("tools", [])}
                & {t.lower() for t in tool_names}
            ]
        return self._format_results(scored, max_chars)

    def retrieve_for_phase(
        self,
        phase: str,
        query: str = "",
        max_chars: int = 1500,
    ) -> str:
        scored = self._score_sections(query, phase=phase)
        return self._format_results(scored, max_chars)


_rag_singleton: LocalRAG | None = None


def _get_rag() -> LocalRAG:
    global _rag_singleton
    if _rag_singleton is None:
        _rag_singleton = LocalRAG()
    return _rag_singleton


def get_rag_context(query: str, max_chars: int = 2500) -> str:
    return _get_rag().retrieve(query, max_chars)


def get_rag_context_for_tools(
    tool_names: list[str],
    query: str = "",
    max_chars: int = 1500,
) -> str:
    return _get_rag().retrieve_for_tools(tool_names, query, max_chars)


def reload_rag() -> None:
    _get_rag().reload()
