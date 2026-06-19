"""Shim: load llm_json from EXPLORATION_KERNEL or use inline fallback."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _load():
    candidates = []
    env = os.environ.get("EXPLORATION_KERNEL", "").strip()
    if env:
        candidates.append(Path(env).resolve() / "python")
    candidates.append(Path.home() / "Documents" / "Libraries" / "exploration-kernel" / "python")
    for directory in candidates:
        if directory.is_dir():
            path_str = str(directory)
            if path_str not in sys.path:
                sys.path.insert(0, path_str)
            break
    try:
        import llm_json as mod  # type: ignore

        return mod
    except ImportError:
        pass

    # Inline minimal fallback (sync with exploration-kernel/python/llm_json.py)
    import json
    import re

    def strip_code_fences(text: str) -> str:
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json|markdown)?\s*\n?", "", text)
            text = re.sub(r"\n?```\s*$", "", text)
        return text.strip()

    def parse_llm_json(text: str) -> dict:
        candidates = [strip_code_fences(text)]
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            candidates.append(text[start : end + 1])
        last_error = None
        for candidate in candidates:
            if not candidate:
                continue
            try:
                return json.loads(candidate)
            except json.JSONDecodeError as e:
                last_error = e
        if last_error:
            raise last_error
        raise json.JSONDecodeError("No JSON object found", text, 0)

    def call_ollama_native_json(**kwargs) -> str:
        import urllib.request

        base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11435").rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]
        payload = {
            "model": kwargs["model"],
            "messages": [
                {"role": "system", "content": kwargs["system_prompt"]},
                {"role": "user", "content": kwargs["user_content"]},
            ],
            "stream": False,
            "format": "json",
            "options": {
                "num_ctx": kwargs["num_ctx"],
                "temperature": kwargs["temperature"],
            },
        }
        req = urllib.request.Request(
            f"{base}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=kwargs.get("timeout", 600)) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        message = (data.get("message") or {}).get("content")
        if not message:
            raise RuntimeError("Ollama devolvió una respuesta vacía")
        return strip_code_fences(message)

    class _Mod:
        parse_llm_json = staticmethod(parse_llm_json)
        call_ollama_native_json = staticmethod(call_ollama_native_json)
        strip_code_fences = staticmethod(strip_code_fences)

    return _Mod()


_mod = _load()
parse_llm_json = _mod.parse_llm_json
call_ollama_native_json = _mod.call_ollama_native_json
strip_code_fences = _mod.strip_code_fences
