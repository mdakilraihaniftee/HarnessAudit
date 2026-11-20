"""Performance instrumentation: request accounting and serving benchmarks.

Two different things are measured and kept separate:

* **Model-serving throughput** (:func:`run_benchmark`): synthetic chat requests
  against the OpenAI-compatible endpoint at fixed concurrency levels — TTFT,
  end-to-end latency, output tokens/s, aggregate throughput, GPU memory and
  utilization sampled through NVML while the load runs.
* **Agentic-task throughput** is measured by the experiment scripts (wall time
  per HarnessAudit run / attack round), using :func:`request_stats` and the
  per-agent Qwen Code usage records for request counts.
"""

from __future__ import annotations

import asyncio
import csv
import json
import random
import statistics
import string
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()
_REQUESTS: list[dict[str, Any]] = []


def record_request(role: str, model: str, usage: Any, *, finish_reason: str | None = None,
                   latency_s: float | None = None) -> None:
    """Record one model request made by this process (judges, attacker)."""
    def _get(name: str) -> int | None:
        if usage is None:
            return None
        value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
        return int(value) if isinstance(value, (int, float)) else None

    with _LOCK:
        _REQUESTS.append({
            "ts": time.time(), "role": role, "model": model,
            "prompt_tokens": _get("prompt_tokens"),
            "completion_tokens": _get("completion_tokens"),
            "finish_reason": finish_reason, "latency_s": latency_s,
        })


def request_stats() -> dict[str, Any]:
    with _LOCK:
        rows = list(_REQUESTS)
    by_role: dict[str, dict[str, Any]] = {}
    for row in rows:
        agg = by_role.setdefault(row["role"], {"requests": 0, "prompt_tokens": 0,
                                               "completion_tokens": 0})
        agg["requests"] += 1
        agg["prompt_tokens"] += row["prompt_tokens"] or 0
        agg["completion_tokens"] += row["completion_tokens"] or 0
    return {"total_requests": len(rows), "by_role": by_role}


def reset_requests() -> None:
    with _LOCK:
        _REQUESTS.clear()


# ------------------------------------------------------------------ GPU sampler


class GpuSampler:
    """Sample NVML memory/utilization for selected GPUs in a background thread."""

    def __init__(self, indices: list[int] | None, interval_s: float = 0.5) -> None:
        self.indices = indices
        self.interval_s = interval_s
        self.samples: list[dict[str, Any]] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _loop(self) -> None:
        from .gpu_discovery import discover

        while not self._stop.is_set():
            try:
                snap = discover()
                now = time.time()
                for dev in snap.devices:
                    if self.indices is None or dev.index in self.indices:
                        self.samples.append({"ts": now, "index": dev.index,
                                             "used_gb": dev.used_gb,
                                             "util": dev.utilization_pct})
            except Exception:  # noqa: BLE001
                pass
            self._stop.wait(self.interval_s)

    def __enter__(self) -> GpuSampler:
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def summary(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for idx in sorted({s["index"] for s in self.samples}):
            rows = [s for s in self.samples if s["index"] == idx]
            utils = [s["util"] for s in rows if s["util"] is not None]
            out[str(idx)] = {
                "samples": len(rows),
                "peak_used_gb": round(max(s["used_gb"] for s in rows), 2),
                "mean_util_pct": round(statistics.fmean(utils), 1) if utils else None,
                "max_util_pct": max(utils) if utils else None,
            }
        return out


# ------------------------------------------------------------------ serving benchmark


@dataclass
class RequestResult:
    ok: bool
    latency_s: float
    ttft_s: float | None
    prompt_tokens: int | None
    output_tokens: int | None
    endpoint: str
    error: str | None = None


@dataclass
class LevelResult:
    label: str
    concurrency: int
    requests: int
    ok: int
    wall_s: float
    latency_p50_s: float | None
    latency_p95_s: float | None
    ttft_p50_s: float | None
    ttft_p95_s: float | None
    mean_output_tokens: float | None
    per_request_output_tok_s: float | None
    aggregate_output_tok_s: float | None
    requests_per_s: float | None
    gpu: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    k = max(0, min(len(values) - 1, int(round(q * (len(values) - 1)))))
    return round(values[k], 4)


def _synthetic_prompt(target_tokens: int, unique: bool) -> str:
    seed = "".join(random.choices(string.ascii_lowercase, k=12)) if unique else "shared"
    filler = (
        "Portfolio review context: client risk profile moderate; holdings include "
        "diversified equity funds, short-duration bonds and cash; compliance requires "
        "suitability checks before any trade; summarize exposures and next steps. "
    )
    words_needed = max(1, int(target_tokens * 0.75))
    body = (filler * (words_needed // len(filler.split()) + 1)).split()[:words_needed]
    return f"[{seed}] " + " ".join(body) + "\nGive a detailed multi-paragraph analysis."


async def _one_request(client: Any, endpoint: str, model: str, prompt: str,
                       max_tokens: int) -> RequestResult:
    start = time.perf_counter()
    ttft = None
    usage = None
    try:
        stream = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=0.7,
            stream=True,
            stream_options={"include_usage": True},
            extra_body={"ignore_eos": True,
                        "chat_template_kwargs": {"enable_thinking": False}},
        )
        async for chunk in stream:
            if ttft is None and chunk.choices and chunk.choices[0].delta and (
                chunk.choices[0].delta.content
            ):
                ttft = time.perf_counter() - start
            if getattr(chunk, "usage", None):
                usage = chunk.usage
        latency = time.perf_counter() - start
        return RequestResult(True, latency, ttft,
                             getattr(usage, "prompt_tokens", None),
                             getattr(usage, "completion_tokens", None), endpoint)
    except Exception as exc:  # noqa: BLE001
        return RequestResult(False, time.perf_counter() - start, None, None, None, endpoint,
                             error=f"{type(exc).__name__}: {exc}"[:300])


async def _run_level(endpoints: list[str], model: str, api_key: str, concurrency: int,
                     n_requests: int, prompt_tokens: int, max_tokens: int,
                     unique_prefix: bool, timeout_s: float) -> tuple[list[RequestResult], float]:
    from openai import AsyncOpenAI

    clients = {ep: AsyncOpenAI(base_url=ep, api_key=api_key, max_retries=0, timeout=timeout_s)
               for ep in endpoints}
    sem = asyncio.Semaphore(concurrency)

    async def task(i: int) -> RequestResult:
        ep = endpoints[i % len(endpoints)]   # client-side round robin across servers
        async with sem:
            return await _one_request(clients[ep], ep, model,
                                      _synthetic_prompt(prompt_tokens, unique_prefix),
                                      max_tokens)

    start = time.perf_counter()
    results = await asyncio.gather(*(task(i) for i in range(n_requests)))
    wall = time.perf_counter() - start
    for client in clients.values():
        await client.close()
    return list(results), wall


def run_benchmark(
    *,
    endpoints: list[str],
    model: str,
    api_key: str,
    concurrency_levels: list[int],
    requests_per_level: int | None = None,
    prompt_tokens: int = 1500,
    max_tokens: int = 256,
    unique_prefix: bool = True,
    gpu_indices: list[int] | None = None,
    label: str = "benchmark",
    timeout_s: float = 600.0,
    warmup: bool = True,
) -> list[LevelResult]:
    out: list[LevelResult] = []
    if warmup:
        asyncio.run(_run_level(endpoints, model, api_key, 1, len(endpoints), 64, 8,
                               True, timeout_s))
    for conc in concurrency_levels:
        n = requests_per_level or max(conc * 2, 4)
        with GpuSampler(gpu_indices) as sampler:
            results, wall = asyncio.run(_run_level(
                endpoints, model, api_key, conc, n, prompt_tokens, max_tokens,
                unique_prefix, timeout_s,
            ))
        ok = [r for r in results if r.ok]
        lat = [r.latency_s for r in ok]
        ttft = [r.ttft_s for r in ok if r.ttft_s is not None]
        toks = [r.output_tokens for r in ok if r.output_tokens]
        per_req = [
            r.output_tokens / (r.latency_s - (r.ttft_s or 0.0))
            for r in ok if r.output_tokens and r.latency_s > (r.ttft_s or 0.0)
        ]
        out.append(LevelResult(
            label=label, concurrency=conc, requests=n, ok=len(ok), wall_s=round(wall, 3),
            latency_p50_s=_pct(lat, 0.5), latency_p95_s=_pct(lat, 0.95),
            ttft_p50_s=_pct(ttft, 0.5), ttft_p95_s=_pct(ttft, 0.95),
            mean_output_tokens=round(statistics.fmean(toks), 1) if toks else None,
            per_request_output_tok_s=round(statistics.fmean(per_req), 2) if per_req else None,
            aggregate_output_tok_s=round(sum(toks) / wall, 2) if toks and wall else None,
            requests_per_s=round(len(ok) / wall, 3) if wall else None,
            gpu=sampler.summary(),
            errors=sorted({r.error for r in results if r.error})[:5],
        ))
    return out


def export_results(results: list[LevelResult], out_dir: Path, *, meta: dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = [asdict(r) for r in results]
    (out_dir / "benchmark.json").write_text(
        json.dumps({"meta": meta, "results": rows}, indent=2), encoding="utf-8"
    )
    flat_fields = [k for k in rows[0] if k not in {"gpu", "errors"}] if rows else []
    with (out_dir / "benchmark.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=flat_fields + ["gpu_peak_used_gb",
                                                                  "gpu_mean_util_pct"])
        writer.writeheader()
        for row in rows:
            gpu = row.get("gpu") or {}
            flat = {k: row[k] for k in flat_fields}
            flat["gpu_peak_used_gb"] = ";".join(
                f"{k}:{v['peak_used_gb']}" for k, v in gpu.items())
            flat["gpu_mean_util_pct"] = ";".join(
                f"{k}:{v['mean_util_pct']}" for k, v in gpu.items())
            writer.writerow(flat)
