"""Strict physical-ID validation for manual Phase 1 runs; no automatic placement."""

from __future__ import annotations

import json
import os
import re
import socket
from pathlib import Path

from multi_agent.local_inference.gpu_allocator import PlacementPolicy, plan_placement
from multi_agent.local_inference.gpu_discovery import discover, permitted_devices
from multi_agent.local_inference.model_registry import model_spec


def parse_ids(text):
    if not text or not re.fullmatch(r"\d+(,\d+)*", text):
        raise ValueError("GPU_IDS is required: use physical nvidia-smi IDs, e.g. 1,3")
    ids = [int(x) for x in text.split(",")]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate GPU IDs")
    return ids


def validate_ids(ids, snapshot, env=None):
    env = dict(os.environ if env is None else env)
    if snapshot.source == "mock" and env.get("RUN_REAL") == "YES":
        raise ValueError("Mock GPU discovery cannot authorize real execution")
    if not snapshot.devices:
        raise ValueError("No GPUs detected; check driver access or obtain a GPU job allocation")
    permitted, reason = permitted_devices(snapshot, selection="explicit", env=env)
    allowed = {p.device.index for p in permitted if p.device is not None}
    # Do not use the existing positional-renumbering heuristic as physical-ID proof.
    cvd = env.get("CUDA_VISIBLE_DEVICES")
    if cvd is not None:
        tokens = cvd.split(",")
        if any(t.isdigit() and snapshot.by_index(int(t)) is None for t in tokens):
            raise ValueError(
                "Ambiguous physical GPU remapping; use UUID-based allocation evidence"
            )
    slurm = snapshot.slurm
    if slurm and slurm.job_id:
        allocation = slurm.step_gpus or slurm.job_gpus
        if allocation:
            allocated = set()
            for t in allocation:
                dev = snapshot.by_index(int(t)) if t.isdigit() else snapshot.by_uuid(t)
                if dev is None:
                    raise ValueError("Cannot reconcile Slurm physical GPU allocation")
                allocated.add(dev.index)
            allowed &= allocated
        elif cvd is None:
            raise ValueError("Slurm allocation has no verifiable GPUs")
    if not set(ids) <= allowed:
        raise ValueError(
            f"Requested GPUs {ids} outside permitted physical IDs {sorted(allowed)}; "
            f"{reason}. Request an allocation containing these GPUs or change GPU_IDS."
        )
    return {
        "requested_gpu_ids": ids,
        "permitted_gpu_ids": sorted(allowed),
        "requested_cuda_mapping": [
            {"cuda_device": i, "physical_gpu": n, "uuid": snapshot.by_index(n).uuid}
            for i, n in enumerate(ids)
        ],
        "inherited_cuda_visible_devices": cvd,
        "permission_reason": reason,
    }


def inspect(config, ids):
    snapshot = discover()
    selection = validate_ids(ids, snapshot)
    spec = model_spec(config.inference.model, config.inference.model_revision)
    if spec is None:
        raise ValueError("Unknown model memory requirements; register model before placement")
    plan = plan_placement(
        snapshot, spec, PlacementPolicy.from_config(config, selection="explicit", explicit_ids=ids)
    )
    return {**selection, "snapshot": snapshot.to_dict(), "placement_plan": plan.to_dict()}


def verify_server(config, ids, *, snapshot=None, records=None, pid_check=None):
    """Reuse only a live, locally recorded vLLM server on explicitly allowed GPUs.

    State records are verified against current UUIDs, process ownership, and live
    compute PIDs (including vLLM worker descendants). Unknown placement fails closed.
    This never changes an already running server's CUDA placement.
    """
    from multi_agent.frameworks.clawteam.process_tree import _descendant_pids
    from multi_agent.local_inference.server import _pid_is_ours, endpoint_ready

    snapshot = snapshot or discover()
    if snapshot.source == "mock" and records is None:
        raise ValueError("Refusing mock GPU discovery for real endpoint verification")
    selection = validate_ids(ids, snapshot)
    if records is None:
        base = (
            Path(
                os.environ.get("HA_SERVER_STATE_DIR")
                or Path.home() / ".cache/harnessaudit/servers"
            )
            / socket.gethostname()
        )
        records = []
        if base.exists():
            for path in base.glob("*.json"):
                try:
                    records.append(json.loads(path.read_text()))
                except (OSError, ValueError):
                    continue
    check = pid_check or _pid_is_ours
    endpoint = config.inference.endpoint.rstrip("/")
    for state in records:
        if state.get("endpoint", "").rstrip("/") != endpoint:
            continue
        if state.get("uid") != os.getuid() or not check(state.get("pid")):
            continue
        mapping = state.get("device_map") or []
        actual = [d.get("nvidia_smi_index") for d in mapping]
        if not actual or not set(actual) <= set(ids):
            raise ValueError(
                f"Existing server uses physical GPUs {actual}, requested {ids}; "
                "start a separate server on permitted GPUs and a different port"
            )
        for d in mapping:
            dev = snapshot.by_index(d["nvidia_smi_index"])
            if dev is None or dev.uuid != d.get("uuid"):
                raise ValueError("Server GPU UUID mapping is stale or unverifiable")
        if (
            state.get("served_name") != config.inference.served_name
            or state.get("model") != config.inference.model
        ):
            raise ValueError("Existing server model differs from experiment configuration")
        if state.get("tensor_parallel_size") != config.inference.tensor_parallel_size and (
            config.inference.tensor_parallel_size != "auto"
        ):
            raise ValueError(
                "Existing server tensor parallelism differs from requested configuration"
            )
        if pid_check is None:
            family = _descendant_pids(int(state["pid"])) | {int(state["pid"])}
            live_gpus = {
                dev.index
                for dev in snapshot.devices
                if any(p.pid in family for p in dev.processes)
            }
            for i in actual:
                dev = snapshot.by_index(i)
                others = [p for p in dev.processes if p.pid not in family]
                if others and not config.gpu.allow_shared_gpu:
                    raise ValueError(
                        "Other workloads share the serving GPU; authorize sharing explicitly"
                    )
                if dev.free_gb < config.gpu.memory_safety_margin_gb:
                    raise ValueError(
                        "Existing server GPU lacks configured free-memory safety margin"
                    )
                if not dev.processes_visible:
                    raise ValueError("Cannot verify server processes on serving GPU")
            if live_gpus != set(actual):
                raise ValueError("Live vLLM GPU process placement does not match server record")
            if not endpoint_ready(endpoint, config.inference.served_name):
                raise ValueError("Recorded local endpoint is not ready")
        return {
            **selection,
            "actual_server_gpu_ids": actual,
            "effective_cuda_devices": state.get("cuda_visible_devices"),
            "actual_server_mapping": mapping,
            "server_record": state,
        }
    raise ValueError(
        "No live verifiable local vLLM server record for MODEL_ENDPOINT; start with "
        "scripts/local_qwen/05_start_server_explicit.sh and matching HA_GPU_IDS"
    )
