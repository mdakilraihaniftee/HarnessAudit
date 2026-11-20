"""Scheduler (Slurm) context: nodes, node names and GPU allocations kept separate.

Node count, node names, per-node GPU ids and total GPU count are distinct
concepts and are never inferred from one another:

* ``num_nodes``      — how many nodes the job holds (``SLURM_JOB_NUM_NODES``)
* ``nodelist``       — their host names (expanded ``SLURM_JOB_NODELIST``)
* ``job_gpus`` /
  ``step_gpus``      — GPU ids allocated *on this node* (``SLURM_JOB_GPUS`` /
                        ``SLURM_STEP_GPUS``); combined with ``CUDA_VISIBLE_DEVICES``
* ``gpus_on_node``   — count on this node (``SLURM_GPUS_ON_NODE``)

Variable semantics differ between Slurm versions and site configurations
(cgroup device constraints renumber GPUs), so :mod:`gpu_discovery` always
prefers ``CUDA_VISIBLE_DEVICES`` and treats Slurm ids as hints.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class SlurmContext:
    job_id: str | None = None
    job_name: str | None = None
    array_task_id: str | None = None
    num_nodes: int | None = None
    nodelist: list[str] = field(default_factory=list)
    node_name: str | None = None
    node_id: int | None = None
    proc_id: int | None = None
    local_id: int | None = None
    ntasks: int | None = None
    gpus_on_node: int | None = None
    job_gpus: list[str] | None = None
    step_gpus: list[str] | None = None
    gpus_per_node: str | None = None
    partition: str | None = None
    account: str | None = None
    submit_dir: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except ValueError:
        return None


def _gpu_list(value: str | None) -> list[str] | None:
    if not value:
        return None
    items = [item.strip() for item in value.split(",") if item.strip()]
    return items or None


def expand_hostlist(expr: str) -> list[str]:
    """Expand Slurm compact host lists, e.g. ``gpu[01-03,07],login1``.

    Uses ``scontrol show hostnames`` when available (authoritative), otherwise a
    pure-Python parser that supports zero padding and nested comma ranges.
    """
    expr = (expr or "").strip()
    if not expr:
        return []
    scontrol = shutil.which("scontrol")
    if scontrol:
        try:
            out = subprocess.run(
                [scontrol, "show", "hostnames", expr],
                capture_output=True, text=True, timeout=10, check=True,
            ).stdout.split()
            if out:
                return out
        except (subprocess.SubprocessError, OSError):
            pass
    hosts: list[str] = []
    # split on commas that are not inside brackets
    parts, depth, buf = [], 0, ""
    for ch in expr:
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(buf)
            buf = ""
        else:
            buf += ch
    if buf:
        parts.append(buf)
    for part in parts:
        match = re.fullmatch(r"([^\[]*)\[([^\]]+)\](.*)", part)
        if not match:
            hosts.append(part)
            continue
        prefix, ranges, suffix = match.groups()
        for rng in ranges.split(","):
            if "-" in rng:
                lo, hi = rng.split("-", 1)
                width = len(lo)
                for num in range(int(lo), int(hi) + 1):
                    hosts.append(f"{prefix}{num:0{width}d}{suffix}")
            else:
                hosts.append(f"{prefix}{rng}{suffix}")
    return hosts


def detect_slurm(env: dict[str, str]) -> SlurmContext | None:
    if not env.get("SLURM_JOB_ID"):
        return None
    return SlurmContext(
        job_id=env.get("SLURM_JOB_ID"),
        job_name=env.get("SLURM_JOB_NAME"),
        array_task_id=env.get("SLURM_ARRAY_TASK_ID"),
        num_nodes=_int(env.get("SLURM_JOB_NUM_NODES") or env.get("SLURM_NNODES")),
        nodelist=expand_hostlist(env.get("SLURM_JOB_NODELIST") or env.get("SLURM_NODELIST", "")),
        node_name=env.get("SLURMD_NODENAME"),
        node_id=_int(env.get("SLURM_NODEID")),
        proc_id=_int(env.get("SLURM_PROCID")),
        local_id=_int(env.get("SLURM_LOCALID")),
        ntasks=_int(env.get("SLURM_NTASKS")),
        gpus_on_node=_int(env.get("SLURM_GPUS_ON_NODE")),
        job_gpus=_gpu_list(env.get("SLURM_JOB_GPUS")),
        step_gpus=_gpu_list(env.get("SLURM_STEP_GPUS")),
        gpus_per_node=env.get("SLURM_GPUS_PER_NODE"),
        partition=env.get("SLURM_JOB_PARTITION"),
        account=env.get("SLURM_JOB_ACCOUNT"),
        submit_dir=env.get("SLURM_SUBMIT_DIR"),
    )


def sbatch_args(cluster: Any) -> list[str]:
    """Translate ``cluster:`` settings into sbatch flags (only those that are set).

    Partition/account names are never invented; unset values are omitted so the
    site defaults apply.
    """
    args: list[str] = [f"--nodes={cluster.nodes}"]
    if cluster.nodelist:
        args.append(f"--nodelist={','.join(cluster.nodelist)}")
    if cluster.exclude:
        args.append(f"--exclude={','.join(cluster.exclude)}")
    if cluster.partition:
        args.append(f"--partition={cluster.partition}")
    if cluster.account:
        args.append(f"--account={cluster.account}")
    if cluster.time:
        args.append(f"--time={cluster.time}")
    if cluster.cpus_per_task:
        args.append(f"--cpus-per-task={cluster.cpus_per_task}")
    if cluster.mem:
        args.append(f"--mem={cluster.mem}")
    if cluster.gpus_per_node:
        gres = f"gpu:{cluster.gpu_type}:{cluster.gpus_per_node}" if cluster.gpu_type else (
            f"gpu:{cluster.gpus_per_node}"
        )
        args.append(f"--gres={gres}")
    if cluster.nodelist and len(cluster.nodelist) < cluster.nodes:
        raise ValueError(
            f"cluster.nodelist names {len(cluster.nodelist)} node(s) but cluster.nodes="
            f"{cluster.nodes}; Slurm would add arbitrary nodes. Make them consistent."
        )
    return args
