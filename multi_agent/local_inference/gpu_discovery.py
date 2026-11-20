"""GPU discovery via NVML (ctypes), with ``nvidia-smi`` and JSON-mock fallbacks.

Nothing here assumes a GPU model or memory size; every value is read from the
driver. ``CUDA_VISIBLE_DEVICES`` and Slurm allocations decide which devices
this process is *permitted* to use (see :func:`permitted_devices`); NVML still
reports every device it can see so ``gpu-status`` can show the whole node.

Set ``HA_GPU_MOCK_FILE=/path/snapshot.json`` to replace the driver with a
fixed snapshot (used by the unit tests and ``scripts/tests/test_gpu_selection.sh``).
"""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import socket
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .cluster import SlurmContext, detect_slurm

GIB = 1024**3


@dataclass
class GpuProcess:
    pid: int
    used_memory_gb: float | None
    uid: int | None = None
    own: bool | None = None
    name: str | None = None


@dataclass
class GpuDevice:
    index: int                    # NVML / nvidia-smi index (PCI bus order)
    uuid: str
    name: str
    total_gb: float
    free_gb: float
    used_gb: float
    utilization_pct: float | None
    memory_utilization_pct: float | None = None
    compute_capability: str | None = None
    mig_enabled: bool = False
    processes: list[GpuProcess] = field(default_factory=list)
    processes_visible: bool = True

    @property
    def foreign_process_count(self) -> int:
        return sum(1 for proc in self.processes if proc.own is not True)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["foreign_process_count"] = self.foreign_process_count
        return data


@dataclass
class GpuSnapshot:
    host: str
    source: str                   # nvml | nvidia-smi | mock | none
    driver_version: str | None
    devices: list[GpuDevice]
    cuda_visible_devices: str | None
    slurm: SlurmContext | None
    error: str | None = None

    def by_index(self, index: int) -> GpuDevice | None:
        return next((dev for dev in self.devices if dev.index == index), None)

    def by_uuid(self, uuid: str) -> GpuDevice | None:
        uuid = uuid.strip()
        return next(
            (dev for dev in self.devices if dev.uuid == uuid or dev.uuid.startswith(uuid)),
            None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "source": self.source,
            "driver_version": self.driver_version,
            "cuda_visible_devices": self.cuda_visible_devices,
            "slurm": self.slurm.to_dict() if self.slurm else None,
            "error": self.error,
            "devices": [dev.to_dict() for dev in self.devices],
        }


# ------------------------------------------------------------------ NVML via ctypes


class _Memory(ctypes.Structure):
    _fields_ = [("total", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong),
                ("used", ctypes.c_ulonglong)]


class _Utilization(ctypes.Structure):
    _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]


class _ProcessInfoV2(ctypes.Structure):
    _fields_ = [("pid", ctypes.c_uint), ("usedGpuMemory", ctypes.c_ulonglong),
                ("gpuInstanceId", ctypes.c_uint), ("computeInstanceId", ctypes.c_uint)]


_NVML_NOT_AVAILABLE = 0xFFFFFFFFFFFFFFFF


def _proc_uid(pid: int) -> int | None:
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("Uid:"):
                return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return None


def _proc_name(pid: int) -> str | None:
    try:
        return Path(f"/proc/{pid}/comm").read_text().strip() or None
    except OSError:
        return None


def _make_process(pid: int, used_bytes: int | None) -> GpuProcess:
    uid = _proc_uid(pid)
    used = None if used_bytes in (None, _NVML_NOT_AVAILABLE) else used_bytes / GIB
    return GpuProcess(
        pid=pid,
        used_memory_gb=round(used, 3) if used is not None else None,
        uid=uid,
        own=(uid == os.getuid()) if uid is not None else None,
        name=_proc_name(pid),
    )


def _nvml_snapshot() -> tuple[str | None, list[GpuDevice]]:
    lib = None
    for name in ("libnvidia-ml.so.1", "libnvidia-ml.so"):
        try:
            lib = ctypes.CDLL(name)
            break
        except OSError:
            continue
    if lib is None:
        raise RuntimeError("libnvidia-ml not found")

    def check(ret: int, what: str) -> None:
        if ret != 0:
            raise RuntimeError(f"NVML {what} failed with code {ret}")

    check(lib.nvmlInit_v2(), "init")
    try:
        driver = ctypes.create_string_buffer(96)
        driver_version = (
            driver.value.decode() if lib.nvmlSystemGetDriverVersion(driver, 96) == 0 else None
        )
        count = ctypes.c_uint()
        check(lib.nvmlDeviceGetCount_v2(ctypes.byref(count)), "device count")
        devices: list[GpuDevice] = []
        for idx in range(count.value):
            handle = ctypes.c_void_p()
            check(lib.nvmlDeviceGetHandleByIndex_v2(idx, ctypes.byref(handle)), "handle")
            name_buf = ctypes.create_string_buffer(96)
            lib.nvmlDeviceGetName(handle, name_buf, 96)
            uuid_buf = ctypes.create_string_buffer(96)
            lib.nvmlDeviceGetUUID(handle, uuid_buf, 96)
            mem = _Memory()
            check(lib.nvmlDeviceGetMemoryInfo(handle, ctypes.byref(mem)), "memory")
            util = _Utilization()
            util_ok = lib.nvmlDeviceGetUtilizationRates(handle, ctypes.byref(util)) == 0
            major, minor = ctypes.c_int(), ctypes.c_int()
            cc = None
            if lib.nvmlDeviceGetCudaComputeCapability(
                handle, ctypes.byref(major), ctypes.byref(minor)
            ) == 0:
                cc = f"{major.value}.{minor.value}"
            current, pending = ctypes.c_uint(), ctypes.c_uint()
            mig = (
                lib.nvmlDeviceGetMigMode(handle, ctypes.byref(current), ctypes.byref(pending))
                == 0 and current.value == 1
            )
            processes: list[GpuProcess] = []
            visible = True
            n = ctypes.c_uint(64)
            buf = (_ProcessInfoV2 * 64)()
            getter = getattr(lib, "nvmlDeviceGetComputeRunningProcesses_v3", None) or getattr(
                lib, "nvmlDeviceGetComputeRunningProcesses_v2", None
            )
            if getter is not None and getter(handle, ctypes.byref(n), buf) == 0:
                for i in range(n.value):
                    processes.append(_make_process(int(buf[i].pid), int(buf[i].usedGpuMemory)))
            else:
                visible = False
            devices.append(
                GpuDevice(
                    index=idx,
                    uuid=uuid_buf.value.decode(),
                    name=name_buf.value.decode(),
                    total_gb=round(mem.total / GIB, 3),
                    free_gb=round(mem.free / GIB, 3),
                    used_gb=round(mem.used / GIB, 3),
                    utilization_pct=float(util.gpu) if util_ok else None,
                    memory_utilization_pct=float(util.memory) if util_ok else None,
                    compute_capability=cc,
                    mig_enabled=mig,
                    processes=processes,
                    processes_visible=visible,
                )
            )
        return driver_version, devices
    finally:
        lib.nvmlShutdown()


def _smi_snapshot() -> tuple[str | None, list[GpuDevice]]:
    smi = shutil.which("nvidia-smi")
    if not smi:
        raise RuntimeError("nvidia-smi not found")
    fields = ("index,uuid,name,memory.total,memory.free,memory.used,utilization.gpu,"
              "utilization.memory,compute_cap,mig.mode.current,driver_version")
    out = subprocess.run(
        [smi, f"--query-gpu={fields}", "--format=csv,noheader,nounits"],
        capture_output=True, text=True, timeout=30, check=True,
    ).stdout
    procs: dict[str, list[GpuProcess]] = {}
    try:
        pout = subprocess.run(
            [smi, "--query-compute-apps=gpu_uuid,pid,used_memory",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=30, check=True,
        ).stdout
        for line in pout.splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 3 and parts[1].isdigit():
                used = float(parts[2]) * 1024**2 if parts[2].replace(".", "").isdigit() else None
                procs.setdefault(parts[0], []).append(
                    _make_process(int(parts[1]), int(used) if used else None)
                )
    except (subprocess.SubprocessError, OSError):
        pass

    def num(value: str) -> float | None:
        try:
            return float(value)
        except ValueError:
            return None

    devices = []
    driver = None
    for line in out.splitlines():
        p = [x.strip() for x in line.split(",")]
        if len(p) < 11:
            continue
        driver = p[10]
        devices.append(
            GpuDevice(
                index=int(p[0]), uuid=p[1], name=p[2],
                total_gb=round((num(p[3]) or 0) / 1024, 3),
                free_gb=round((num(p[4]) or 0) / 1024, 3),
                used_gb=round((num(p[5]) or 0) / 1024, 3),
                utilization_pct=num(p[6]), memory_utilization_pct=num(p[7]),
                compute_capability=p[8] or None,
                mig_enabled=p[9].lower() == "enabled",
                processes=procs.get(p[1], []),
            )
        )
    return driver, devices


def _mock_snapshot(path: str) -> tuple[str | None, list[GpuDevice], str]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    devices = []
    for raw in data.get("devices", []):
        processes = [GpuProcess(**proc) for proc in raw.get("processes", [])]
        fields = {k: v for k, v in raw.items() if k not in {"processes", "foreign_process_count"}}
        devices.append(GpuDevice(processes=processes, **fields))
    return data.get("driver_version"), devices, data.get("host") or "mock-host"


def discover(env: dict[str, str] | None = None) -> GpuSnapshot:
    """Take a snapshot of the GPUs on this node."""
    env_map = dict(os.environ if env is None else env)
    host = socket.gethostname()
    slurm = detect_slurm(env_map)
    cvd = env_map.get("CUDA_VISIBLE_DEVICES")
    mock = env_map.get("HA_GPU_MOCK_FILE")
    if mock:
        driver, devices, mock_host = _mock_snapshot(mock)
        return GpuSnapshot(mock_host, "mock", driver, devices, cvd, slurm)
    errors = []
    for source, fn in (("nvml", _nvml_snapshot), ("nvidia-smi", _smi_snapshot)):
        try:
            driver, devices = fn()
            return GpuSnapshot(host, source, driver, devices, cvd, slurm)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{source}: {exc}")
    return GpuSnapshot(host, "none", None, [], cvd, slurm, error="; ".join(errors))


# ------------------------------------------------------------------ permitted devices


@dataclass
class PermittedDevice:
    """A device this process may use, plus the token to put in CUDA_VISIBLE_DEVICES."""

    token: str                  # exact CUDA_VISIBLE_DEVICES token for a child process
    device: GpuDevice | None    # matched NVML device (None when it cannot be matched)
    source: str                 # all | cuda_visible_devices | slurm

    def to_dict(self) -> dict[str, Any]:
        return {
            "token": self.token,
            "source": self.source,
            "index": self.device.index if self.device else None,
            "uuid": self.device.uuid if self.device else None,
        }


def _match_tokens(tokens: list[str], snapshot: GpuSnapshot) -> list[GpuDevice | None]:
    matched: list[GpuDevice | None] = []
    ints = [t for t in tokens if t.isdigit()]
    # Inside a cgroup that only exposes the allocated devices, NVML renumbers them
    # 0..n-1 while the parent may still list physical ids. Fall back to position.
    renumbered = bool(ints) and len(snapshot.devices) == len(tokens) and any(
        snapshot.by_index(int(t)) is None for t in ints
    )
    for pos, token in enumerate(tokens):
        if token.startswith(("GPU-", "MIG-")):
            matched.append(snapshot.by_uuid(token))
        elif token.isdigit():
            if renumbered:
                matched.append(snapshot.devices[pos] if pos < len(snapshot.devices) else None)
            else:
                matched.append(snapshot.by_index(int(token)))
        else:
            matched.append(None)
    return matched


def _split_tokens(value: str) -> list[str]:
    return [tok.strip() for tok in value.split(",") if tok.strip()]


def permitted_devices(
    snapshot: GpuSnapshot,
    *,
    selection: str = "auto",
    env: dict[str, str] | None = None,
) -> tuple[list[PermittedDevice], str]:
    """Devices this process may use and a human-readable explanation.

    * An inherited ``CUDA_VISIBLE_DEVICES`` always restricts the set.
    * In a Slurm job (or ``selection=scheduler``) only allocated GPUs are used;
      an allocation without GPUs yields an empty list (callers fail closed).
    * Otherwise every NVML device is permitted (subject to the placement policy).
    """
    env_map = dict(os.environ if env is None else env)
    cvd = env_map.get("CUDA_VISIBLE_DEVICES")
    slurm = snapshot.slurm
    in_job = bool(slurm and slurm.job_id)

    if cvd is not None:
        tokens = _split_tokens(cvd)
        if not tokens or tokens == ["-1"] or cvd.strip().lower() in {"none", "nodevfiles"}:
            return [], f"CUDA_VISIBLE_DEVICES={cvd!r} hides every GPU"
        matched = _match_tokens(tokens, snapshot)
        return (
            [PermittedDevice(t, d, "cuda_visible_devices") for t, d in zip(tokens, matched)],
            f"restricted by CUDA_VISIBLE_DEVICES={cvd}",
        )

    if in_job or selection == "scheduler":
        gpus = (slurm.step_gpus or slurm.job_gpus) if slurm else None
        if not gpus:
            return [], (
                "running under a scheduler allocation with no GPUs "
                "(CUDA_VISIBLE_DEVICES/SLURM_STEP_GPUS/SLURM_JOB_GPUS unset)"
            )
        tokens = [str(g) for g in gpus]
        matched = _match_tokens(tokens, snapshot)
        return (
            [PermittedDevice(t, d, "slurm") for t, d in zip(tokens, matched)],
            f"restricted by Slurm allocation {tokens}",
        )

    return (
        [PermittedDevice(str(dev.index), dev, "all") for dev in snapshot.devices],
        "no scheduler or CUDA_VISIBLE_DEVICES restriction; all node GPUs considered",
    )


def format_status(snapshot: GpuSnapshot, permitted: list[PermittedDevice], why: str) -> str:
    lines = [
        f"host: {snapshot.host}   source: {snapshot.source}   driver: "
        f"{snapshot.driver_version or 'n/a'}"
    ]
    if snapshot.error:
        lines.append(f"error: {snapshot.error}")
    if snapshot.slurm and snapshot.slurm.job_id:
        s = snapshot.slurm
        lines.append(
            f"slurm: job {s.job_id} nodes={s.num_nodes} nodelist={','.join(s.nodelist)} "
            f"node_id={s.node_id} local_id={s.local_id} job_gpus={s.job_gpus} "
            f"step_gpus={s.step_gpus}"
        )
    lines.append(f"permitted: {why}")
    allowed_idx = {p.device.index for p in permitted if p.device is not None}
    if not snapshot.devices:
        lines.append("no GPUs detected")
    for dev in snapshot.devices:
        util = f"{dev.utilization_pct:.0f}%" if dev.utilization_pct is not None else "n/a"
        procs = (
            f"{len(dev.processes)} proc ({dev.foreign_process_count} not ours)"
            if dev.processes_visible else "procs not visible"
        )
        flags = []
        if dev.index not in allowed_idx:
            flags.append("NOT PERMITTED")
        if dev.mig_enabled:
            flags.append("MIG")
        lines.append(
            f"GPU {dev.index}: {dev.name}, free {dev.free_gb:.1f} GB / {dev.total_gb:.1f} GB, "
            f"utilization {util}, {procs}, cc {dev.compute_capability or 'n/a'}"
            + (f"  [{', '.join(flags)}]" if flags else "")
        )
    return "\n".join(lines)
