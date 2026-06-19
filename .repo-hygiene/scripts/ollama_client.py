#!/usr/bin/env python3
"""Thin Ollama chat client with per-model context profiles, metrics, and VRAM-friendly unload."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

import requests

HYGIENE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_METRICS_PATH = HYGIENE_DIR / ".reports" / "hygiene" / "ollama_metrics.jsonl"


@dataclass
class ChatResult:
    content: str
    prompt_eval_count: int | None = None
    eval_count: int | None = None
    total_duration_ms: int | None = None
    load_duration_ms: int | None = None
    ctx_saturation: float | None = None
    client_truncated: bool = False


def _ns_to_ms(value: int | None) -> int | None:
    if value is None:
        return None
    return int(value / 1_000_000)


def log_metrics(
    entry: dict,
    *,
    metrics_path: Path | None = None,
) -> None:
    path = metrics_path or DEFAULT_METRICS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    entry.setdefault("timestamp", int(time.time() * 1000))
    entry.setdefault("source", "repo-hygiene")
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def chat(
    ollama_url: str,
    model_profile: dict,
    system_prompt: str,
    user_message: str,
    *,
    temperature: float | None = None,
    timeout: int = 2400,
    unload_after: bool = True,
    client_truncated: bool = False,
    metrics_path: Path | None = None,
    metrics_extra: dict | None = None,
) -> str:
    """Single chat completion. Returns assistant text; logs metrics to JSONL."""
    result = chat_with_metrics(
        ollama_url,
        model_profile,
        system_prompt,
        user_message,
        temperature=temperature,
        timeout=timeout,
        unload_after=unload_after,
        client_truncated=client_truncated,
        metrics_path=metrics_path,
        metrics_extra=metrics_extra,
    )
    return result.content


def chat_with_metrics(
    ollama_url: str,
    model_profile: dict,
    system_prompt: str,
    user_message: str,
    *,
    temperature: float | None = None,
    timeout: int = 2400,
    unload_after: bool = True,
    client_truncated: bool = False,
    metrics_path: Path | None = None,
    metrics_extra: dict | None = None,
) -> ChatResult:
    """Single chat completion with structured metrics."""
    url = f"{ollama_url.rstrip('/')}/api/chat"
    num_ctx = int(model_profile.get("num_ctx", 8192))
    temp = temperature if temperature is not None else model_profile.get("temperature", 0.2)
    payload = {
        "model": model_profile["model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "stream": False,
        "options": {
            "temperature": temp,
            "num_ctx": num_ctx,
        },
    }
    if unload_after:
        payload["keep_alive"] = 0

    response = requests.post(url, json=payload, timeout=timeout)
    response.raise_for_status()
    data = response.json()

    prompt_eval = data.get("prompt_eval_count")
    eval_count = data.get("eval_count")
    saturation = (
        round(prompt_eval / num_ctx, 3)
        if prompt_eval is not None and num_ctx
        else None
    )

    result = ChatResult(
        content=data["message"]["content"],
        prompt_eval_count=prompt_eval,
        eval_count=eval_count,
        total_duration_ms=_ns_to_ms(data.get("total_duration")),
        load_duration_ms=_ns_to_ms(data.get("load_duration")),
        ctx_saturation=saturation,
        client_truncated=client_truncated,
    )

    entry = {
        "model": model_profile["model"],
        "num_ctx": num_ctx,
        "prompt_eval_count": prompt_eval,
        "eval_count": eval_count,
        "ctx_saturation": saturation,
        "client_truncated": client_truncated,
        "total_duration_ms": result.total_duration_ms,
        "load_duration_ms": result.load_duration_ms,
        "unload_after": unload_after,
    }
    if metrics_extra:
        entry.update(metrics_extra)
    log_metrics(entry, metrics_path=metrics_path)

    return result


def unload_model(ollama_url: str, model: str, *, timeout: int = 30) -> None:
    """Drop a model from VRAM (keep_alive=0). Safe to call if already unloaded."""
    try:
        requests.post(
            f"{ollama_url.rstrip('/')}/api/generate",
            json={"model": model, "keep_alive": 0},
            timeout=timeout,
        )
    except requests.RequestException:
        pass


def chat_json(
    ollama_url: str,
    model_profile: dict,
    system_prompt: str,
    user_message: str,
    *,
    temperature: float | None = None,
    timeout: int = 2400,
    unload_after: bool = True,
    metrics_path: Path | None = None,
    metrics_extra: dict | None = None,
) -> str:
    """Ollama chat with format=json for structured agent output."""
    from llm_json import call_ollama_native_json

    num_ctx = int(model_profile.get("num_ctx", 8192))
    temp = temperature if temperature is not None else model_profile.get("temperature", 0.1)
    t0 = time.time()
    try:
        content = call_ollama_native_json(
            model=model_profile["model"],
            system_prompt=system_prompt,
            user_content=user_message,
            temperature=float(temp),
            num_ctx=num_ctx,
            timeout=timeout,
        )
    except Exception:
        raise
    entry = {
        "model": model_profile["model"],
        "num_ctx": num_ctx,
        "json_mode": True,
        "total_duration_ms": int((time.time() - t0) * 1000),
        "unload_after": unload_after,
    }
    if metrics_extra:
        entry.update(metrics_extra)
    log_metrics(entry, metrics_path=metrics_path)
    if unload_after:
        try:
            requests.post(
                f"{ollama_url.rstrip('/')}/api/generate",
                json={"model": model_profile["model"], "keep_alive": 0},
                timeout=30,
            )
        except requests.RequestException:
            pass
    return content
