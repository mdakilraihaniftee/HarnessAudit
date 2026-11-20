"""Reproducibility metadata recorded with every experiment.

Local-Qwen runs change the target agent, the attacker and (optionally) the
judge, so results must never be pooled with hosted-model results by accident.
Every run report gets an ``inference_provenance`` block built here.
"""

from __future__ import annotations

import functools
import hashlib
import os
import subprocess
from pathlib import Path
from typing import Any

from .config import REPO_ROOT, LocalInferenceConfig, current_config
from .router import resolve_route


@functools.lru_cache(maxsize=1)
def git_commit() -> dict[str, Any]:
    def run(*args: str) -> str | None:
        try:
            out = subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True,
                                 text=True, timeout=10)
            return out.stdout.strip() if out.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            return None

    status = run("status", "--porcelain", "--untracked-files=no")
    return {"commit": run("rev-parse", "HEAD"), "dirty": bool(status)}


def file_sha256(path: str | Path) -> str | None:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


@functools.lru_cache(maxsize=4)
def _qwen_code_version(binary: str) -> str | None:
    try:
        out = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=60)
        lines = [ln for ln in out.stdout.splitlines() if ln.strip()]
        return lines[-1].strip() if lines else None
    except (OSError, subprocess.SubprocessError):
        return None


def _server_record(endpoint: str) -> dict[str, Any] | None:
    try:
        from .server import list_servers
    except ImportError:
        return None
    for state in list_servers():
        if state.get("endpoint", "").rstrip("/") == endpoint.rstrip("/"):
            return {k: state.get(k) for k in (
                "name", "vllm_version", "tensor_parallel_size", "replicas",
                "gpu_memory_utilization", "max_model_len", "max_num_seqs", "device_map",
                "cuda_visible_devices", "startup_s", "host", "command")}
    return None


def _server_sampling(endpoint: str) -> dict[str, Any]:
    """Effective sampling defaults of the serving process, from its launch command."""
    import json as _json

    record = _server_record(endpoint) or {}
    cmd = record.get("command") or []
    out: dict[str, Any] = {"source": "server launch command" if cmd else "unknown"}
    for flag, key in (("--override-generation-config", "override_generation_config"),
                      ("--default-chat-template-kwargs", "default_chat_template_kwargs")):
        if flag in cmd:
            try:
                out[key] = _json.loads(cmd[cmd.index(flag) + 1])
            except (ValueError, IndexError):
                out[key] = cmd[cmd.index(flag) + 1]
    if cmd and "--override-generation-config" not in cmd:
        out["override_generation_config"] = (
            "none: model generation_config.json defaults apply "
            "(Qwen3-8B: temperature 0.6, top_p 0.95, top_k 20)")
    return out


def inference_provenance(
    *,
    harness: str | None = None,
    framework: str | None = None,
    task_path: str | Path | None = None,
    target_model: str | None = None,
    judge_model: str | None = None,
    skip_judge: bool | None = None,
    attacker_backend: str | None = None,
    config: LocalInferenceConfig | None = None,
) -> dict[str, Any]:
    cfg = config or current_config()
    inf = cfg.inference
    record: dict[str, Any] = {
        "inference_mode": inf.mode,
        "harness": harness,
        "framework": framework,
        "git": git_commit(),
        "task_yaml_sha256": file_sha256(task_path) if task_path else None,
        "config_sources": cfg.sources,
    }
    local_harness = harness == "qwen_code"
    if inf.mode == "paid_opt_in" and not local_harness:
        record.update({
            "target_model": target_model,
            "judge_model": None if skip_judge else judge_model,
            "endpoint_type": "hosted (paid_opt_in)",
            "attacker_backend": attacker_backend,
        })
        return record

    from .model_registry import local_snapshot, model_spec

    spec = model_spec(inf.model, inf.model_revision)
    snap = local_snapshot(inf.model, inf.model_revision)
    target = resolve_route("target_specialists", cfg)
    judge = resolve_route("completion_judge", cfg)
    attacker = resolve_route("attacker_generator", cfg)
    from multi_agent.frameworks.native.qwen_code.runtime import resolve_qwen_bin

    try:
        qbin = resolve_qwen_bin(cfg.harness.qwen_code.bin)
        qver = _qwen_code_version(qbin)
    except RuntimeError:
        qbin, qver = None, None
    record.update({
        "model_id": inf.model,
        "model_revision": snap.name if snap else inf.model_revision,
        "model_license": spec.license if spec else None,
        "quantization": inf.quantization,
        "backend": inf.backend,
        "endpoint": target.endpoint,
        "endpoint_type": target.endpoint_class,
        "server": _server_record(target.endpoint),
        "qwen_code_version": qver,
        "context_length": inf.context_length,
        "tensor_parallel_size_config": inf.tensor_parallel_size,
        "pipeline_parallel_size": inf.pipeline_parallel_size,
        # qwen_code agents send no sampling parameters (--bare ignores settings),
        # so the effective values are the server defaults recorded here.
        "agent_sampling": _server_sampling(target.endpoint),
        "configured_sampling": {"temperature": target.temperature, "top_p": inf.top_p,
                                "top_k": inf.top_k, "reasoning_mode": target.reasoning_mode},
        "target_model": target.model,
        "target_coordinator_model": resolve_route("target_coordinator", cfg).model,
        "judge_model": None if skip_judge else (judge_model or judge.model),
        "judge_endpoint_type": None if skip_judge else judge.endpoint_class,
        "judge_temperature": None if skip_judge else judge.temperature,
        "attacker_backend": attacker_backend,
        "attacker_model": attacker.model if attacker_backend not in (None, "mock") else None,
        "harness_config": cfg.harness.model_dump(mode="json"),
        "hostname": os.uname().nodename,
        "comparability_note": (
            "Local open-weight model results; not comparable to hosted GPT/Claude "
            "HarnessAudit results without stating the model change."
        ),
    })
    return record
