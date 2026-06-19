"""
core/rag.py — Semantic RAG engine: FAISS + BAAI/bge-small-en-v1.5 (CPU).

Scans knowledge/ recursively (including knowledge/tools/ playbooks and any
extra_dirs from config.yaml), parses YAML frontmatter for tool/phase tags,
and retrieves the most semantically relevant sections.

Falls back gracefully to Jaccard word-overlap if sentence-transformers or
faiss-cpu are not installed (e.g., during the first boot before `pip install`).

Public interface is UNCHANGED from the previous Jaccard implementation:
  get_rag_context(query, max_chars) -> str
  get_rag_context_for_tools(tool_names, query, max_chars) -> str
  reload_rag() -> None
"""

from __future__ import annotations

import logging
import pickle
import re
from pathlib import Path
from typing import Any

from core.runtime_paths import app_root

logger = logging.getLogger("pwsh_agent.core.rag")

_KNOWLEDGE_DIR = app_root() / "knowledge"
_CACHE_DIR = _KNOWLEDGE_DIR / ".faiss_cache"

# Embedding model — small (~130 MB), good multilingual coverage for a personal RAG.
_EMBED_MODEL_NAME = "BAAI/bge-small-en-v1.5"

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


# ─────────────────────────────────────────────────────────────────────────────
# Config helpers
# ─────────────────────────────────────────────────────────────────────────────

def _extra_knowledge_dirs() -> list[Path]:
    """Optional extra RAG roots from config.yaml rag.extra_dirs."""
    cfg_path = app_root() / "config.yaml"
    if not cfg_path.exists():
        return []
    try:
        import yaml
        with open(cfg_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        raw_dirs = cfg.get("rag", {}).get("extra_dirs", []) or []
        dirs: list[Path] = []
        for raw in raw_dirs:
            p = Path(str(raw)).expanduser()
            if p.is_dir():
                dirs.append(p.resolve())
        return dirs
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Frontmatter parser (unchanged from original)
# ─────────────────────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────────────────────
# Jaccard fallback (kept in place — used when FAISS is unavailable)
# ─────────────────────────────────────────────────────────────────────────────

def _tokenize(text: str) -> set[str]:
    words = re.findall(r"\b[a-zA-Z0-9_-]+\b", text.lower())
    return set(words)


def _jaccard_score(
    query_tokens: set[str],
    sec: dict[str, Any],
    tool_set: set[str],
    phase: str | None,
) -> float:
    intersection = query_tokens.intersection(sec["tokens"])
    union = query_tokens.union(sec["tokens"])
    score = len(intersection) / len(union) if union and query_tokens else 0.0

    title_tokens = _tokenize(f"{sec['doc_title']} {sec['section_title']}")
    title_intersection = query_tokens.intersection(title_tokens)
    if title_intersection:
        score += 0.1 * len(title_intersection)

    sec_tools = {t.lower() for t in sec.get("tools", [])}
    if tool_set and sec_tools.intersection(tool_set):
        score += 0.25 * len(sec_tools.intersection(tool_set))

    if phase and phase.lower() in [p.lower() for p in sec.get("phase", [])]:
        score += 0.2

    return score


# ─────────────────────────────────────────────────────────────────────────────
# Main RAG class
# ─────────────────────────────────────────────────────────────────────────────

class LocalRAG:
    """
    In-process semantic RAG using FAISS + BGE-Small embeddings.

    On first load it builds the index (slow, ~seconds).
    The index is cached to disk at knowledge/.faiss_cache/ so subsequent
    starts are instant. The cache is invalidated when any .md file changes.

    Falls back to Jaccard automatically if sentence-transformers/faiss-cpu
    are not installed.
    """

    def __init__(
        self,
        knowledge_dir: Path = _KNOWLEDGE_DIR,
        extra_dirs: list[Path] | None = None,
    ):
        self.knowledge_dir = knowledge_dir
        self.extra_dirs = extra_dirs if extra_dirs is not None else _extra_knowledge_dirs()
        self.sections: list[dict[str, Any]] = []
        self._embedder = None
        self._index = None          # faiss.Index or None
        self._use_faiss = False
        self._load_knowledge_base()

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def reload(self) -> None:
        self.sections.clear()
        self._index = None
        self._load_knowledge_base()

    # ── Loading ──────────────────────────────────────────────────────────────

    def _all_md_paths(self) -> list[Path]:
        roots: list[tuple[Path, str | None]] = [(self.knowledge_dir, None)]
        for extra in self.extra_dirs:
            roots.append((extra, extra.name))
        paths: list[Path] = []
        for root, _ in roots:
            if root.exists():
                paths.extend(sorted(root.rglob("*.md")))
        return paths

    def _load_knowledge_base(self) -> None:
        roots: list[tuple[Path, str | None]] = [(self.knowledge_dir, None)]
        for extra in self.extra_dirs:
            roots.append((extra, extra.name))

        for root, label in roots:
            if not root.exists():
                continue
            self._load_from_root(root, label)

        self._try_build_faiss_index()

    def _load_from_root(self, root: Path, label: str | None) -> None:
        for path in sorted(root.rglob("*.md")):
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
                try:
                    rel = path.relative_to(root)
                except ValueError:
                    rel = path.name
                rel_norm = str(rel).replace("\\", "/")
                if label:
                    rel_norm = f"{label}/{rel_norm}"

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
                        anchor = f"{rel_norm}#{section_slug}-{idx}"
                        self.sections.append({
                            "file": rel_norm,
                            "doc_title": doc_title,
                            "section_title": sec_title,
                            "anchor": anchor,
                            "paragraph_index": idx,
                            "content": chunk,
                            # Jaccard tokens kept for fallback scoring
                            "tokens": _tokenize(chunk),
                            "tools": list(file_tools),
                            "phase": list(file_phase),
                        })
            except Exception:
                pass

    # ── FAISS index ──────────────────────────────────────────────────────────

    def _cache_key(self) -> str:
        """Stable key based on all .md mtimes — cache is invalidated on any edit."""
        import hashlib
        h = hashlib.md5()
        for p in self._all_md_paths():
            try:
                h.update(f"{p}:{p.stat().st_mtime}".encode())
            except OSError:
                pass
        return h.hexdigest()

    def _cache_paths(self, key: str) -> tuple[Path, Path]:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        return _CACHE_DIR / f"{key}.faiss", _CACHE_DIR / f"{key}.pkl"

    def _try_load_cache(self, key: str) -> bool:
        import faiss  # noqa: F401
        faiss_path, pkl_path = self._cache_paths(key)
        if not faiss_path.is_file() or not pkl_path.is_file():
            return False
        try:
            import faiss as _faiss
            self._index = _faiss.read_index(str(faiss_path))
            with open(pkl_path, "rb") as f:
                vecs = pickle.load(f)
            # Validate cache matches current sections count
            if self._index.ntotal != len(self.sections):
                logger.debug("FAISS cache stale (section count mismatch), rebuilding.")
                self._index = None
                return False
            logger.debug("FAISS cache loaded (%d vectors).", self._index.ntotal)
            return True
        except Exception as e:
            logger.debug("FAISS cache load failed: %s", e)
            return False

    def _save_cache(self, key: str, vecs: Any) -> None:
        try:
            import faiss as _faiss
            faiss_path, pkl_path = self._cache_paths(key)
            _faiss.write_index(self._index, str(faiss_path))
            with open(pkl_path, "wb") as f:
                pickle.dump(vecs, f)
            logger.debug("FAISS cache saved to %s", faiss_path)
        except Exception as e:
            logger.debug("FAISS cache save failed: %s", e)

    def _try_build_faiss_index(self) -> None:
        if not self.sections:
            return
        try:
            import faiss
            from sentence_transformers import SentenceTransformer
            import numpy as np
        except ImportError:
            logger.info(
                "faiss-cpu or sentence-transformers not installed — "
                "falling back to Jaccard RAG. Run: pip install faiss-cpu sentence-transformers"
            )
            return

        try:
            key = self._cache_key()
            if self._try_load_cache(key):
                if self._embedder is None:
                    self._embedder = SentenceTransformer(_EMBED_MODEL_NAME, device="cpu")
                self._use_faiss = True
                return

            logger.info(
                "Building FAISS index for %d chunks with %s (first run, please wait)…",
                len(self.sections),
                _EMBED_MODEL_NAME,
            )
            if self._embedder is None:
                self._embedder = SentenceTransformer(_EMBED_MODEL_NAME, device="cpu")

            texts = [s["content"] for s in self.sections]
            vecs = self._embedder.encode(
                texts,
                batch_size=32,
                show_progress_bar=False,
                normalize_embeddings=True,
            ).astype("float32")

            dim = vecs.shape[1]
            index = faiss.IndexFlatIP(dim)  # inner product = cosine on normalized vecs
            index.add(vecs)
            self._index = index
            self._use_faiss = True

            self._save_cache(key, vecs)
            logger.info("FAISS index built and cached (%d vectors, dim=%d).", index.ntotal, dim)

        except Exception as e:
            logger.warning("FAISS index build failed (%s) — falling back to Jaccard.", e)
            self._use_faiss = False
            self._index = None

    # ── Retrieval ─────────────────────────────────────────────────────────────

    def _faiss_retrieve(
        self,
        query: str,
        top_k: int,
        tool_set: set[str],
        phase: str | None,
    ) -> list[tuple[float, dict[str, Any]]]:
        """Return top_k sections sorted by cosine similarity with metadata bonuses."""
        import numpy as np

        q_vec = self._embedder.encode(
            [query],
            normalize_embeddings=True,
            show_progress_bar=False,
        ).astype("float32")

        k = min(top_k * 4, len(self.sections))  # over-fetch then re-rank
        scores, indices = self._index.search(q_vec, k)
        scores = scores[0].tolist()
        indices = indices[0].tolist()

        results: list[tuple[float, dict[str, Any]]] = []
        for score, idx in zip(scores, indices):
            if idx < 0 or idx >= len(self.sections):
                continue
            sec = self.sections[idx]
            bonus = 0.0
            sec_tools = {t.lower() for t in sec.get("tools", [])}
            if tool_set and sec_tools.intersection(tool_set):
                bonus += 0.15 * len(sec_tools.intersection(tool_set))
            if phase and phase.lower() in [p.lower() for p in sec.get("phase", [])]:
                bonus += 0.1
            results.append((score + bonus, sec))

        results.sort(key=lambda x: x[0], reverse=True)
        return results[:top_k]

    def _jaccard_retrieve(
        self,
        query: str,
        tool_names: list[str] | None,
        phase: str | None,
    ) -> list[tuple[float, dict[str, Any]]]:
        query_tokens = _tokenize(query)
        tool_set = {t.lower() for t in (tool_names or [])}
        if not query_tokens and not tool_set and not phase:
            return []

        scored: list[tuple[float, dict[str, Any]]] = []
        for sec in self.sections:
            score = _jaccard_score(query_tokens, sec, tool_set, phase)
            sec_tools = {t.lower() for t in sec.get("tools", [])}
            if score > 0 or (tool_set and sec_tools.intersection(tool_set)):
                if score <= 0 and tool_set and sec_tools.intersection(tool_set):
                    score = 0.15
                scored.append((score, sec))

        scored.sort(key=lambda x: x[0], reverse=True)
        return scored

    # ── Public retrieval API ──────────────────────────────────────────────────

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
        if not query.strip():
            return ""
        top_k = 6
        if self._use_faiss and self._index is not None:
            scored = self._faiss_retrieve(query, top_k, set(), None)
        else:
            scored = self._jaccard_retrieve(query, None, None)
        return self._format_results(scored, max_chars)

    def retrieve_for_tools(
        self,
        tool_names: list[str],
        query: str = "",
        max_chars: int = 1500,
    ) -> str:
        tool_set = {t.lower() for t in tool_names}
        top_k = 6

        if self._use_faiss and self._index is not None and query.strip():
            scored = self._faiss_retrieve(query, top_k, tool_set, None)
        else:
            scored = self._jaccard_retrieve(query, tool_names, None)
            if not scored and tool_names:
                scored = [
                    (0.1, sec)
                    for sec in self.sections
                    if {t.lower() for t in sec.get("tools", [])} & tool_set
                ]
        return self._format_results(scored, max_chars)

    def retrieve_for_phase(
        self,
        phase: str,
        query: str = "",
        max_chars: int = 1500,
    ) -> str:
        top_k = 6
        if self._use_faiss and self._index is not None and query.strip():
            scored = self._faiss_retrieve(query, top_k, set(), phase)
        else:
            scored = self._jaccard_retrieve(query, None, phase)
        return self._format_results(scored, max_chars)


# ─────────────────────────────────────────────────────────────────────────────
# Singleton + public API (identical to previous implementation)
# ─────────────────────────────────────────────────────────────────────────────

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
