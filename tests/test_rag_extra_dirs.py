"""Tests for RAG extra_dirs support."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.rag import LocalRAG


def test_rag_loads_extra_dir(tmp_path):
    extra = tmp_path / "extra_feed"
    extra.mkdir()
    (extra / "note.md").write_text(
        "---\ntools: [hygiene_lookup]\nphase: [hygiene]\n---\n\n# Feed chunk\n\nHygiene eyes test content.\n",
        encoding="utf-8",
    )
    rag = LocalRAG(extra_dirs=[extra])
    result = rag.retrieve_for_tools(["hygiene_lookup"], "hygiene eyes", max_chars=2000)
    assert "Hygiene eyes" in result or "hygiene" in result.lower()
