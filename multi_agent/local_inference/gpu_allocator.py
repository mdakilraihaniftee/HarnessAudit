"""Memory-aware GPU placement for local model servers.

Selection modes
---------------
``auto``       choose among permitted GPUs using live free memory/utilization.
``explicit``   use exactly the listed nvidia-smi indices (``--gpu-ids 0,2,3``);
               an id outside the permitted set is an error, never a fallback.
``scheduler``  only GPUs granted by Slurm (``CUDA_VISIBLE_DEVICES``/``SLURM_*``).

Policy
------
* A GPU with other users' processes (or memory used by others) is *occupied*
  and only eligible with ``allow_shared_gpu``.
* A GPU above ``max_gpu_utilization`` is never selected.
* Usable budget per GPU = min(gpu_memory_utilization x total, free - margin);
  the estimated requirement must fit in that budget.
* Tensor parallelism is used only when the model does not fit on one GPU (or
  when explicitly requested) and only with sizes the model architecture allows.
  Extra GPUs become data-parallel replicas, never an invalid TP group.

Reservations
------------
``GpuReservations`` stops two HarnessAudit launches on the same node from
racing for the same GPU (O_EXCL lock files + an flock around placement). They
do **not** coordinate with other users' software; that is what the live
free-memory check and the safety margin are for.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import math
import os
import socket
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterator

from .gpu_discovery import GpuSnapshot, PermittedDevice, permitted_devices
from .gpu_memory import MemoryEstimate, estimate
from .model_registry import ModelSpec

OCCUPIED_FOREIGN_USED_GB = 1.0   # memory used by others above this marks a GPU occupied
IDLE_UTILIZATION_PCT = 5.0


@dataclass
class PlacementPolicy:
    selection: str = "auto"
    explicit_ids: list[int] | None = None
    allow_shared_gpu: bool = False
    prefer_idle_gpu: bool = True
    min_free_memory_gb: float = 0.0
    max_gpu_utilization: float = 30.0
    memory_safety_margin_gb: float = 6.0
    gpu_memory_utilization: float = 0.80
    tensor_parallel_size: int | str = "auto"
    data_parallel_replicas: int | str = 1
    max_model_len: int = 32768
    max_num_seqs: int = 16
    max_num_batched_tokens: int | None = 8192
    runtime_overhead_gb: float = 2.5

    @classmethod
    def from_config(cls, cfg: Any, **overrides: Any) -> PlacementPolicy:
        inf, gpu = cfg.inference, cfg.gpu
        policy = cls(
            selection=gpu.selection,
            explicit_ids=gpu.explicit_ids,
            allow_shared_gpu=gpu.allow_shared_gpu,
            prefer_idle_gpu=gpu.prefer_idle_gpu,
            min_free_memory_gb=gpu.min_free_memory_gb,
            max_gpu_utilization=gpu.max_gpu_utilization,
            memory_safety_margin_gb=gpu.memory_safety_margin_gb,
            gpu_memory_utilization=inf.gpu_memory_utilization,
            tensor_parallel_size=inf.tensor_parallel_size,
            data_parallel_replicas=inf.data_parallel_replicas,
            max_model_len=inf.context_length,
            max_num_seqs=inf.max_num_seqs,
            max_num_batched_tokens=inf.max_num_batched_tokens,
        )
        for key, value in overrides.items():
            if value is not None:
                setattr(policy, key, value)
        return policy


@dataclass
class CandidateGpu:
    token: str
    index: int | None
    uuid: str | None
    name: str | None
    total_gb: float | None
    free_gb: float | None
    utilization_pct: float | None
    occupied: bool
    idle: bool
    budget_gb: float | None
    eligible: bool = True
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PlacementPlan:
    ok: bool
    selection: str
    permitted_reason: str
    tensor_parallel_size: int | None = None
    replicas: int = 0
    groups: list[list[str]] = field(default_factory=list)   # CVD tokens per replica
    gpu_memory_utilization: float | None = None
    estimate: MemoryEstimate | None = None
    kv_capacity_tokens: int | None = None
    max_full_sequences: float | None = None
    candidates: list[CandidateGpu] = field(default_factory=list)
    device_map: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def tokens(self) -> list[str]:
        return [tok for group in self.groups for tok in group]

    @property
    def cuda_visible_devices(self) -> str:
        return ",".join(self.tokens)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["estimate"] = self.estimate.to_dict() if self.estimate else None
        data["cuda_visible_devices"] = self.cuda_visible_devices
        return data


def _candidate(
    permitted: PermittedDevice, policy: PlacementPolicy, reserved: dict[str, dict[str, Any]],
) -> CandidateGpu:
    dev = permitted.device
    if dev is None:
        return CandidateGpu(
            token=permitted.token, index=None, uuid=None, name=None, total_gb=None,
            free_gb=None, utilization_pct=None, occupied=True, idle=False, budget_gb=None,
            eligible=False, reasons=["memory statistics unavailable for this device"],
        )
    own_used = sum((p.used_memory_gb or 0.0) for p in dev.processes if p.own is True)
    foreign_used = max(0.0, dev.used_gb - own_used)
    occupied = dev.foreign_process_count > 0 or foreign_used > OCCUPIED_FOREIGN_USED_GB
    util = dev.utilization_pct
    idle = (not occupied) and (util is None or util <= IDLE_UTILIZATION_PCT)
    budget = min(policy.gpu_memory_utilization * dev.total_gb,
                 dev.free_gb - policy.memory_safety_margin_gb)
    cand = CandidateGpu(
        token=permitted.token, index=dev.index, uuid=dev.uuid, name=dev.name,
        total_gb=dev.total_gb, free_gb=dev.free_gb, utilization_pct=util,
        occupied=occupied, idle=idle, budget_gb=round(budget, 2),
    )
    if dev.mig_enabled and not permitted.token.startswith("MIG-"):
        cand.eligible = False
        cand.reasons.append("MIG mode enabled; pass a MIG device UUID explicitly")
    if util is not None and util > policy.max_gpu_utilization:
        cand.eligible = False
        cand.reasons.append(
            f"utilization {util:.0f}% > max_gpu_utilization {policy.max_gpu_utilization:.0f}%"
        )
    if occupied and not policy.allow_shared_gpu:
        cand.eligible = False
        cand.reasons.append(
            f"in use by other processes ({dev.foreign_process_count} foreign proc, "
            f"{foreign_used:.1f} GB); set gpu.allow_shared_gpu=true if site policy allows"
        )
    if dev.free_gb < policy.min_free_memory_gb:
        cand.eligible = False
        cand.reasons.append(
            f"free {dev.free_gb:.1f} GB < min_free_memory_gb {policy.min_free_memory_gb}"
        )
    key = dev.uuid or permitted.token
    if key in reserved:
        holder = reserved[key]
        cand.eligible = False
        cand.reasons.append(
            f"reserved by another HarnessAudit launch (pid {holder.get('pid')}, "
            f"{holder.get('label')})"
        )
    return cand


def _rank(c: CandidateGpu, prefer_idle: bool) -> tuple:
    return (
        0 if (c.idle or not prefer_idle) else 1,
        -(c.budget_gb or 0.0),
        c.utilization_pct if c.utilization_pct is not None else 100.0,
        c.index if c.index is not None else 1_000,
    )


def plan_placement(
    snapshot: GpuSnapshot,
    spec: ModelSpec,
    policy: PlacementPolicy,
    *,
    reserved: dict[str, dict[str, Any]] | None = None,
    env: dict[str, str] | None = None,
) -> PlacementPlan:
    permitted, why = permitted_devices(snapshot, selection=policy.selection, env=env)
    plan = PlacementPlan(ok=False, selection=policy.selection, permitted_reason=why)
    if not permitted:
        plan.errors.append(f"No permitted GPUs: {why}. Refusing to start (no CPU fallback).")
        return plan

    if policy.selection == "explicit":
        ids = policy.explicit_ids or []
        if not ids:
            plan.errors.append("gpu.selection=explicit requires --gpu-ids / gpu.explicit_ids")
            return plan
        if len(set(ids)) != len(ids):
            plan.errors.append(f"Duplicate GPU ids in {ids}")
            return plan
        by_index = {p.device.index: p for p in permitted if p.device is not None}
        missing = [i for i in ids if i not in by_index]
        if missing:
            plan.errors.append(
                f"Requested GPU id(s) {missing} are not permitted for this process "
                f"({why}; permitted indices: {sorted(by_index)})."
            )
            return plan
        permitted = [by_index[i] for i in ids]

    reserved = reserved or {}
    cands = [_candidate(p, policy, reserved) for p in permitted]
    plan.candidates = cands

    valid_tp = spec.valid_tp_sizes(max_tp=max(1, len(cands)))
    if policy.tensor_parallel_size != "auto":
        tp_req = int(policy.tensor_parallel_size)
        if tp_req not in spec.valid_tp_sizes(max_tp=64):
            plan.errors.append(
                f"tensor_parallel_size={tp_req} is invalid for {spec.model_id} "
                f"({spec.num_attention_heads} attention heads, {spec.num_kv_heads} KV heads); "
                f"valid sizes: {spec.valid_tp_sizes(max_tp=8)}"
            )
            return plan
        tp_options = [tp_req]
    else:
        tp_options = valid_tp

    chosen_tp = None
    chosen_est = None
    fitting: list[CandidateGpu] = []
    for tp in tp_options:
        est = estimate(
            spec, tensor_parallel_size=tp, max_model_len=policy.max_model_len,
            max_num_seqs=policy.max_num_seqs,
            max_num_batched_tokens=policy.max_num_batched_tokens,
            runtime_overhead_gb=policy.runtime_overhead_gb,
        )
        fits = [c for c in cands if c.eligible and (c.budget_gb or 0) >= est.required_min_gb]
        if len(fits) >= tp:
            chosen_tp, chosen_est, fitting = tp, est, fits
            break
    if chosen_tp is None:
        est1 = estimate(
            spec, tensor_parallel_size=tp_options[0], max_model_len=policy.max_model_len,
            max_num_seqs=policy.max_num_seqs,
            max_num_batched_tokens=policy.max_num_batched_tokens,
            runtime_overhead_gb=policy.runtime_overhead_gb,
        )
        plan.estimate = est1
        eligible = [c for c in cands if c.eligible]
        for c in cands:
            if c.eligible and (c.budget_gb or 0) < est1.required_min_gb:
                c.reasons.append(
                    f"budget {c.budget_gb:.1f} GB < required {est1.required_min_gb:.1f} GB "
                    f"(TP={tp_options[0]})"
                )
        plan.errors.append(
            f"Insufficient GPU memory for {spec.model_id} with max_model_len="
            f"{policy.max_model_len}: tried TP sizes {tp_options}; "
            f"{len(eligible)} eligible GPU(s). Lower context_length/max_num_seqs, use a "
            "smaller or quantized model, or permit more GPUs."
        )
        return plan

    # Group: prefer homogeneous GPU models inside a TP group.
    fitting.sort(key=lambda c: _rank(c, policy.prefer_idle_gpu))
    by_name: dict[str, list[CandidateGpu]] = {}
    for c in fitting:
        by_name.setdefault(c.name or "?", []).append(c)

    if policy.data_parallel_replicas == "auto":
        # "auto" uses every GPU the user listed or the scheduler granted; on a
        # shared workstation in auto mode it stays conservative (one replica).
        replicas_wanted = (
            max(1, len(fitting) // chosen_tp)
            if policy.selection in {"explicit", "scheduler"} else 1
        )
    else:
        replicas_wanted = int(policy.data_parallel_replicas)

    groups: list[list[CandidateGpu]] = []
    for name in sorted(by_name, key=lambda n: _rank(by_name[n][0], policy.prefer_idle_gpu)):
        pool = by_name[name]
        while len(pool) >= chosen_tp and len(groups) < replicas_wanted:
            groups.append(pool[:chosen_tp])
            pool = pool[chosen_tp:]
        by_name[name] = pool
    if len(groups) < replicas_wanted:
        if policy.data_parallel_replicas != "auto":
            plan.errors.append(
                f"Requested {replicas_wanted} replica(s) x TP={chosen_tp} but only "
                f"{len(groups)} homogeneous group(s) of eligible GPUs fit."
            )
            plan.estimate = chosen_est
            return plan
    if len({c.name for g in groups for c in g}) > 1:
        plan.warnings.append(
            "Replicas run on different GPU models; throughput will differ per replica."
        )

    used = {c.token for g in groups for c in g}
    unused = [c for c in cands if c.token not in used]
    if policy.selection == "explicit" and unused:
        details = "; ".join(
            f"GPU {c.index}: " + ("; ".join(c.reasons) if c.reasons else
                                  f"not needed for TP={chosen_tp} x {len(groups)} replica(s)")
            for c in unused
        )
        plan.warnings.append(
            f"Explicitly listed GPU(s) left unused — {details}. No invalid tensor-parallel "
            "group was formed; set --replicas auto to use every eligible listed GPU."
        )

    chosen = [c for g in groups for c in g]
    free_cap = min(
        ((c.free_gb or 0) - policy.memory_safety_margin_gb) / (c.total_gb or 1) for c in chosen
    )
    gmu = min(policy.gpu_memory_utilization, free_cap)
    gmu = max(0.05, math.floor(gmu * 100 + 1e-3) / 100.0)
    min_total = min(c.total_gb or 0 for c in chosen)
    kv_budget = (gmu * min_total - chosen_est.weights_gb - chosen_est.runtime_overhead_gb
                 - chosen_est.activation_gb)
    plan.ok = True
    plan.tensor_parallel_size = chosen_tp
    plan.replicas = len(groups)
    plan.groups = [[c.token for c in g] for g in groups]
    plan.gpu_memory_utilization = gmu
    plan.estimate = chosen_est
    plan.kv_capacity_tokens = chosen_est.kv_tokens_for(kv_budget)
    plan.max_full_sequences = round(plan.kv_capacity_tokens / policy.max_model_len, 2)
    if chosen_est.required_target_gb > gmu * min_total:
        plan.warnings.append(
            f"KV cache below target: ~{plan.max_full_sequences} full-length sequences fit; "
            "concurrent long agent conversations may queue."
        )
    for local_idx, c in enumerate(chosen):
        plan.device_map.append({
            "cuda_device": f"cuda:{local_idx}",
            "visible_token": c.token,
            "nvidia_smi_index": c.index,
            "uuid": c.uuid,
            "name": c.name,
            "replica": local_idx // chosen_tp,
        })
    return plan


def format_plan(plan: PlacementPlan) -> str:
    lines = [f"selection: {plan.selection}   permitted: {plan.permitted_reason}"]
    for c in plan.candidates:
        state = "eligible" if c.eligible else "excluded"
        util = f"{c.utilization_pct:.0f}%" if c.utilization_pct is not None else "n/a"
        lines.append(
            f"  GPU {c.index} [{c.token}] {c.name}: free {c.free_gb} GB, util {util}, "
            f"budget {c.budget_gb} GB, {'occupied' if c.occupied else 'not occupied'}"
            f" -> {state}" + (f" ({'; '.join(c.reasons)})" if c.reasons else "")
        )
    if plan.estimate:
        e = plan.estimate
        lines.append(
            f"estimate (TP={e.tensor_parallel_size}, per GPU): weights {e.weights_gb:.1f} GB + "
            f"KV(min) {e.kv_min_gb:.1f} GB + overhead {e.runtime_overhead_gb:.1f} GB + "
            f"activations {e.activation_gb:.1f} GB = {e.required_min_gb:.1f} GB"
        )
    if plan.ok:
        lines.append(
            f"PLAN: TP={plan.tensor_parallel_size} x replicas={plan.replicas}  "
            f"CUDA_VISIBLE_DEVICES={plan.cuda_visible_devices}  "
            f"gpu_memory_utilization={plan.gpu_memory_utilization:.2f}  "
            f"KV capacity ~{plan.kv_capacity_tokens} tokens "
            f"(~{plan.max_full_sequences} x max_model_len)"
        )
        for d in plan.device_map:
            lines.append(
                f"  {d['cuda_device']} (replica {d['replica']}) -> nvidia-smi GPU "
                f"{d['nvidia_smi_index']} {d['uuid']}"
            )
    for w in plan.warnings:
        lines.append(f"WARNING: {w}")
    for e in plan.errors:
        lines.append(f"ERROR: {e}")
    return "\n".join(lines)


# ------------------------------------------------------------------ reservations


def default_lock_dir() -> Path:
    base = os.environ.get("HA_GPU_LOCK_DIR")
    if base:
        return Path(base)
    return Path.home() / ".cache" / "harnessaudit" / "gpu_locks"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class GpuReservations:
    """Per-node cooperative reservations for this project's own launches."""

    def __init__(self, lock_dir: str | Path | None = None, host: str | None = None) -> None:
        self.host = host or socket.gethostname()
        self.root = Path(lock_dir or default_lock_dir()) / self.host
        self.root.mkdir(parents=True, exist_ok=True)

    @contextlib.contextmanager
    def placement_lock(self, timeout: float = 120.0) -> Iterator[None]:
        """Serialize plan+reserve across concurrent HarnessAudit launches on a node."""
        path = self.root / ".placement.lock"
        with path.open("a+") as handle:
            deadline = time.monotonic() + timeout
            while True:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() > deadline:
                        raise TimeoutError(f"Timed out waiting for placement lock {path}")
                    time.sleep(0.2)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def _file(self, key: str) -> Path:
        safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in key)
        return self.root / f"{safe}.json"

    def active(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for path in self.root.glob("*.json"):
            try:
                data = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            pid = int(data.get("pid") or 0)
            if pid and not _pid_alive(pid):
                path.unlink(missing_ok=True)       # stale: holder is gone
                continue
            out[data.get("key", path.stem)] = data
        return out

    def reserve(self, keys: list[str], *, label: str, pid: int | None = None) -> list[Path]:
        made: list[Path] = []
        try:
            for key in keys:
                path = self._file(key)
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(fd, "w") as handle:
                    json.dump({"key": key, "pid": pid or os.getpid(), "label": label,
                               "host": self.host, "created": time.time(),
                               "user": os.getuid()}, handle)
                made.append(path)
        except FileExistsError as exc:
            for path in made:
                path.unlink(missing_ok=True)
            raise RuntimeError(f"GPU {exc.filename} is already reserved") from exc
        return made

    def update_pid(self, keys: list[str], pid: int) -> None:
        for key in keys:
            path = self._file(key)
            try:
                data = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            data["pid"] = pid
            path.write_text(json.dumps(data))

    def release(self, keys: list[str]) -> None:
        for key in keys:
            self._file(key).unlink(missing_ok=True)
