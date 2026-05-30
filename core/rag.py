"""
core/rag.py — Local lightweight Jaccard word-overlap RAG engine.

Scans the c:\\Users\\soyko\\Documents\\pwsh_agent\\knowledge\\ directory,
tokenizes sections by markdown headers, and retrieves the most relevant
reference material matching any query (mission objective or user input).
"""

import os
import re
from pathlib import Path
from typing import List, Dict, Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_KNOWLEDGE_DIR = _PROJECT_ROOT / "knowledge"


class LocalRAG:
    """
    In-process micro RAG system using section-level word-overlap retrieval.
    Requires no external packages or database engines.
    """

    def __init__(self, knowledge_dir: Path = _KNOWLEDGE_DIR):
        self.knowledge_dir = knowledge_dir
        self.sections: List[Dict[str, Any]] = []
        self._load_knowledge_base()

    def _tokenize(self, text: str) -> set[str]:
        """Convert text into lowercase word tokens, stripping punctuation."""
        words = re.findall(r"\b[a-zA-Z0-9_-]+\b", text.lower())
        return set(words)

    def _load_knowledge_base(self):
        """Scan and segment all markdown files in the knowledge directory."""
        if not self.knowledge_dir.exists():
            return

        for path in self.knowledge_dir.glob("*.md"):
            try:
                content = path.read_text(encoding="utf-8")
                # Split the document by markdown headers
                parts = re.split(r"(?=(?:^|\n)#+\s+)", content)
                
                doc_title = path.stem.replace("_", " ").title()
                
                for part in parts:
                    part = part.strip()
                    if not part:
                        continue
                    
                    # Extract header title as section name if possible
                    title_match = re.match(r"^#+\s+(.+)", part)
                    sec_title = title_match.group(1).strip() if title_match else "General Reference"
                    
                    tokens = self._tokenize(part)
                    self.sections.append({
                        "file": path.name,
                        "doc_title": doc_title,
                        "section_title": sec_title,
                        "content": part,
                        "tokens": tokens
                    })
            except Exception:
                pass

    def retrieve(self, query: str, max_chars: int = 2500) -> str:
        """
        Scoring each section based on word-overlap similarity to query tokens.
        Concatenates the highest-scoring sections up to max_chars limit.
        """
        query_tokens = self._tokenize(query)
        if not query_tokens or not self.sections:
            return ""

        scored_sections = []
        for sec in self.sections:
            intersection = query_tokens.intersection(sec["tokens"])
            union = query_tokens.union(sec["tokens"])
            
            # Use Jaccard Similarity Coefficient: |A ∩ B| / |A ∪ B|
            score = len(intersection) / len(union) if union else 0.0
            
            # Give a slight boost if query words match the section/document titles
            title_tokens = self._tokenize(f"{sec['doc_title']} {sec['section_title']}")
            title_intersection = query_tokens.intersection(title_tokens)
            if title_intersection:
                score += 0.1 * len(title_intersection)

            if score > 0:
                scored_sections.append((score, sec))

        # Sort descending by score
        scored_sections.sort(key=lambda x: x[0], reverse=True)

        result_parts = []
        total_len = 0
        
        for score, sec in scored_sections:
            formatted_sec = (
                f"--- REFERENCE SOURCE: {sec['doc_title']} -> {sec['section_title']} ---\n"
                f"{sec['content']}\n"
            )
            if total_len + len(formatted_sec) > max_chars:
                # Add partial or stop
                if not result_parts:
                    result_parts.append(formatted_sec[:max_chars])
                break
            result_parts.append(formatted_sec)
            total_len += len(formatted_sec)

        return "\n".join(result_parts).strip()


# Shared singleton instance
_rag_singleton = None

def get_rag_context(query: str, max_chars: int = 2500) -> str:
    """Fetch matching reference guide context for LLM prompt augmentation."""
    global _rag_singleton
    if _rag_singleton is None:
        _rag_singleton = LocalRAG()
    return _rag_singleton.retrieve(query, max_chars)
