#!/usr/bin/env python3
"""Parallel Phase-1 cluster execution against a single ollama-code instance."""

from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable


@dataclass
class ConcurrencyConfig:
    enabled: bool
    max_workers: int
    ollama_num_parallel: int


def resolve_concurrency(ai_config: dict) -> ConcurrencyConfig:
    block = ai_config.get("concurrency", {})
    enabled = bool(block.get("enabled", False))
    max_workers = int(block.get("max_workers", 3))
    ollama_num_parallel = int(block.get("ollama_num_parallel", max_workers))
    max_workers = max(1, min(max_workers, ollama_num_parallel))
    return ConcurrencyConfig(
        enabled=enabled,
        max_workers=max_workers,
        ollama_num_parallel=ollama_num_parallel,
    )


def cluster_unload_policy(
    unload_after_call: bool,
    concurrency: ConcurrencyConfig,
) -> tuple[bool, bool]:
    """
    Returns (unload_during_cluster_calls, unload_after_cluster_batch).

    Parallel coder slots need the model resident; unload once before planner synthesis.
    """
    if concurrency.enabled:
        return False, True
    return unload_after_call, False


def run_cluster_batch(
    jobs: list[tuple[int, list[str]]],
    worker: Callable[[int, list[str]], tuple[int, list[str], str]],
    *,
    concurrency: ConcurrencyConfig,
    on_info: Callable[[str], None] | None = None,
) -> list[tuple[list[str], str]]:
    """Run cluster jobs; preserve input order in results."""
    total = len(jobs)
    if total == 0:
        return []

    if not concurrency.enabled or total == 1:
        ordered: dict[int, tuple[list[str], str]] = {}
        for idx, cluster_files in jobs:
            i, files, report = worker(idx, cluster_files)
            ordered[i] = (files, report)
        return [ordered[i] for i in range(1, total + 1)]

    workers = min(concurrency.max_workers, total)
    if on_info:
        on_info(
            f"Fase 1 en paralelo: {workers} workers "
            f"(ollama-code OLLAMA_NUM_PARALLEL={concurrency.ollama_num_parallel})"
        )

    ordered: dict[int, tuple[list[str], str]] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(worker, idx, cluster_files): idx
            for idx, cluster_files in jobs
        }
        for fut in as_completed(futures):
            idx = futures[fut]
            try:
                i, files, report = fut.result()
                ordered[i] = (files, report)
            except Exception as exc:
                cluster_files = next(cf for j, cf in jobs if j == idx)
                ordered[idx] = (
                    cluster_files,
                    f"*Error al procesar cluster {idx}: {exc}*",
                )
                print(
                    f"[ERROR] Cluster {idx} falló en pool: {exc}",
                    file=sys.stderr,
                )
    return [ordered[i] for i in range(1, total + 1)]
