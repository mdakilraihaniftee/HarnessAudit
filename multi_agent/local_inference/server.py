"""Launch, track and stop *our own* local vLLM servers.

Guarantees
----------
* Devices are chosen (and reserved) before the model loads; a loaded server is
  never "migrated" — changing GPUs means a controlled stop + restart.
* Every flag we pass is checked against the installed ``vllm serve --help=all``.
* Weights are never downloaded implicitly: ``HF_HUB_OFFLINE=1`` unless
  ``inference.allow_model_download`` is true.
* vLLM usage statistics are disabled (``VLLM_NO_USAGE_STATS=1``).
* ``stop`` only signals a process group recorded in our own state file, owned by
  this user, whose command line is a vLLM server. Other processes are never
  touched, whatever GPU they are on.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .config import REPO_ROOT, LocalInferenceConfig
from .endpoints import COMMERCIAL_ENV_VARS
from .gpu_allocator import GpuReservations, PlacementPlan, PlacementPolicy, plan_placement
from .gpu_discovery import discover
from .model_registry import local_snapshot, model_spec, with_quantization

log = logging.getLogger(__name__)

OOM_PATTERNS = (
    r"CUDA out of memory", r"OutOfMemoryError", r"out of memory",
    r"No available memory for the cache blocks",
    r"less than desired GPU memory utilization",
    r"Insufficient memory", r"not enough KV cache",
    r"larger than the maximum number of tokens that can be stored in KV cache",
)


def state_dir() -> Path:
    base = os.environ.get("HA_SERVER_STATE_DIR")
    root = Path(base) if base else Path.home() / ".cache" / "harnessaudit" / "servers"
    path = root / socket.gethostname()
    path.mkdir(parents=True, exist_ok=True)
    return path


def vllm_bin() -> Path:
    explicit = os.environ.get("HA_VLLM_BIN")
    if explicit:
        return Path(explicit)
    venv = os.environ.get("HA_VLLM_VENV") or str(
        Path.home() / "venvs" / f"HarnessAudit-vllm-{os.uname().machine}"
    )
    candidate = Path(venv) / "bin" / "vllm"
    if candidate.exists():
        return candidate
    found = shutil.which("vllm")
    if found:
        return Path(found)
    raise RuntimeError(
        f"vLLM not found (looked in {candidate} and PATH). Install it with "
        "scripts/local_qwen/03_setup_qwen.sh --allow-download --only vllm, or set HA_VLLM_VENV."
    )


def _help_text(binary: Path) -> str:
    cache = state_dir() / f"vllm_help_{abs(hash(str(binary.resolve()))) % 10**8}.txt"
    if cache.exists() and cache.stat().st_mtime >= binary.resolve().stat().st_mtime:
        return cache.read_text()
    env = dict(os.environ, PATH=f"{binary.parent}:{os.environ.get('PATH', '')}",
               VLLM_NO_USAGE_STATS="1")
    out = subprocess.run([str(binary), "serve", "--help=all"], capture_output=True,
                         text=True, timeout=300, env=env)
    text = out.stdout + out.stderr
    if "--max-model-len" not in text:
        raise RuntimeError(f"Could not read `vllm serve --help=all`: {text[-800:]}")
    cache.write_text(text)
    return text


def vllm_version(binary: Path) -> str | None:
    python = binary.parent / "python"
    if not python.exists():
        return None
    try:
        return subprocess.run([str(python), "-c", "import vllm;print(vllm.__version__)"],
                              capture_output=True, text=True, timeout=120).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


@dataclass
class ServerSpec:
    name: str
    model: str
    served_name: str
    host: str
    port: int
    tensor_parallel_size: int
    pipeline_parallel_size: int
    data_parallel_size: int
    max_model_len: int
    gpu_memory_utilization: float
    max_num_seqs: int
    max_num_batched_tokens: int | None
    tool_call_parser: str
    reasoning_parser: str | None
    reasoning_mode: str
    dtype: str
    quantization: str | None
    revision: str | None
    enforce_eager: bool = False
    sampling_defaults: dict[str, Any] = field(default_factory=dict)
    api_key_env: str | None = None
    extra_args: list[str] = field(default_factory=list)

    def command(self, binary: Path) -> list[str]:
        cmd = [
            str(binary), "serve", self.model,
            "--host", self.host, "--port", str(self.port),
            "--served-model-name", self.served_name,
            "--max-model-len", str(self.max_model_len),
            "--gpu-memory-utilization", f"{self.gpu_memory_utilization:.2f}",
            "--max-num-seqs", str(self.max_num_seqs),
            "--tensor-parallel-size", str(self.tensor_parallel_size),
            "--pipeline-parallel-size", str(self.pipeline_parallel_size),
            "--dtype", self.dtype,
            "--enable-auto-tool-choice", "--tool-call-parser", self.tool_call_parser,
            "--default-chat-template-kwargs",
            json.dumps({"enable_thinking": self.reasoning_mode == "on"}),
            "--seed", "0",
        ]
        if self.reasoning_parser:
            cmd += ["--reasoning-parser", self.reasoning_parser]
        if self.sampling_defaults:
            # Server-side defaults for requests that send no sampling parameters.
            # Qwen Code in --bare mode sends none (it ignores settings.json
            # generationConfig), so this is what the agents actually use.
            cmd += ["--override-generation-config", json.dumps(self.sampling_defaults)]
        if self.max_num_batched_tokens:
            cmd += ["--max-num-batched-tokens", str(self.max_num_batched_tokens)]
        if self.data_parallel_size > 1:
            cmd += ["--data-parallel-size", str(self.data_parallel_size)]
        if self.quantization:
            cmd += ["--quantization", self.quantization]
        if self.revision:
            cmd += ["--revision", self.revision]
        if self.enforce_eager:
            cmd += ["--enforce-eager"]
        if self.api_key_env and os.environ.get(self.api_key_env):
            cmd += ["--api-key", os.environ[self.api_key_env]]
        return cmd + list(self.extra_args)


def validate_flags(cmd: list[str], help_text: str) -> list[str]:
    """Return flags in ``cmd`` that the installed vLLM does not advertise."""
    missing = []
    for token in cmd[3:]:
        if token.startswith("--"):
            flag = token.split("=", 1)[0]
            if not re.search(rf"(?m)^\s+{re.escape(flag)}[\s,=]", help_text) and not re.search(
                rf"[\s,]{re.escape(flag)}[\s,=]", help_text
            ):
                missing.append(flag)
    return missing


def _redacted(cmd: list[str]) -> list[str]:
    out, hide = [], False
    for tok in cmd:
        out.append("***" if hide else tok)
        hide = tok == "--api-key"
    return out


def server_env(plan: PlacementPlan, binary: Path, *, allow_download: bool) -> dict[str, str]:
    env = dict(os.environ)
    for name in COMMERCIAL_ENV_VARS:      # the server never needs hosted credentials
        env.pop(name, None)
    env.update({
        "CUDA_DEVICE_ORDER": "PCI_BUS_ID",
        "CUDA_VISIBLE_DEVICES": plan.cuda_visible_devices,
        "PATH": f"{binary.parent}:{os.environ.get('PATH', '')}",
        "VLLM_NO_USAGE_STATS": "1",
        "DO_NOT_TRACK": "1",
        "TOKENIZERS_PARALLELISM": "false",
    })
    if not allow_download:
        env["HF_HUB_OFFLINE"] = "1"
        env["TRANSFORMERS_OFFLINE"] = "1"
    return env


def _http_ok(url: str, timeout: float = 3.0) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 - loopback
            return resp.status == 200, resp.read(4096).decode(errors="replace")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return False, str(exc)


def endpoint_ready(base_url: str, served_name: str) -> bool:
    ok, body = _http_ok(base_url.rstrip("/") + "/models")
    return ok and served_name in body


def _tail(path: Path, n: int = 60) -> str:
    try:
        return "\n".join(path.read_text(errors="replace").splitlines()[-n:])
    except OSError:
        return ""


def classify_failure(log_text: str) -> str:
    if any(re.search(p, log_text, re.I) for p in OOM_PATTERNS):
        return "oom"
    if re.search(r"Address already in use|port .* in use", log_text, re.I):
        return "port_in_use"
    if re.search(r"offline mode|LocalEntryNotFoundError|couldn't find .* cached", log_text, re.I):
        return "model_not_cached"
    return "error"


def port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False


@dataclass
class LaunchResult:
    ok: bool
    name: str
    endpoint: str | None = None
    pid: int | None = None
    plan: dict[str, Any] | None = None
    attempts: list[dict[str, Any]] = field(default_factory=list)
    log_path: str | None = None
    startup_s: float | None = None
    error: str | None = None
    reused: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def find_running(cfg: LocalInferenceConfig) -> dict[str, Any] | None:
    """An existing, healthy server of ours (or any) that already serves this model."""
    inf = cfg.inference
    for path in sorted(state_dir().glob("*.json")):
        state = _load_state(path)
        if not state or not _pid_is_ours(state.get("pid")):
            continue
        if state.get("served_name") == inf.served_name and endpoint_ready(
            state["endpoint"], inf.served_name
        ):
            return state
    if endpoint_ready(inf.endpoint, inf.served_name):
        return {"endpoint": inf.endpoint, "served_name": inf.served_name, "external": True}
    return None


def launch(
    cfg: LocalInferenceConfig,
    *,
    name: str | None = None,
    policy_overrides: dict[str, Any] | None = None,
    log_dir: Path | None = None,
    reuse: bool = True,
    max_retries: int = 2,
    dry_run: bool = False,
) -> LaunchResult:
    inf = cfg.inference
    name = name or f"qwen-{inf.port}"
    if reuse and not dry_run:
        running = find_running(cfg)
        if running:
            return LaunchResult(ok=True, name=running.get("name", "external"),
                                endpoint=running["endpoint"], pid=running.get("pid"),
                                reused=True)
    if inf.backend != "vllm":
        return LaunchResult(ok=False, name=name, error=(
            f"backend={inf.backend!r}: launching is only implemented for vllm. Start the "
            "server yourself and point inference.endpoint at it."))
    spec = model_spec(inf.model, inf.model_revision)
    if spec is None:
        return LaunchResult(ok=False, name=name, error=(
            f"Unknown model {inf.model!r}: not in the registry and not cached locally, so "
            "memory cannot be estimated. Download it first (setup --allow-download)."))
    spec = with_quantization(spec, inf.quantization)
    if local_snapshot(inf.model, inf.model_revision) is None and not inf.allow_model_download:
        return LaunchResult(ok=False, name=name, error=(
            f"Model {inf.model} is not in the local Hugging Face cache and "
            "inference.allow_model_download=false. Run scripts/local_qwen/03_setup_qwen.sh "
            "--allow-download --only model (HA_MODEL=...)."))
    if not port_free(inf.host, inf.port) and not dry_run:
        return LaunchResult(ok=False, name=name, error=(
            f"Port {inf.host}:{inf.port} is already in use and is not a healthy server for "
            f"{inf.served_name}. Choose another inference.port."))

    binary = vllm_bin()
    help_text = _help_text(binary)
    policy = PlacementPolicy.from_config(cfg, **(policy_overrides or {}))
    reservations = GpuReservations(cfg.gpu.lock_dir)
    log_dir = log_dir or (REPO_ROOT / "results" / "local_qwen" / "servers")
    log_dir.mkdir(parents=True, exist_ok=True)
    result = LaunchResult(ok=False, name=name)

    seqs, batched, eager = policy.max_num_seqs, policy.max_num_batched_tokens, False
    for attempt in range(max_retries + 1):
        with reservations.placement_lock():
            snapshot = discover()
            policy.max_num_seqs, policy.max_num_batched_tokens = seqs, batched
            plan = plan_placement(snapshot, spec, policy, reserved=reservations.active())
            if not plan.ok:
                result.error = "; ".join(plan.errors)
                result.plan = plan.to_dict()
                return result
            assert plan.tensor_parallel_size is not None
            sspec = ServerSpec(
                name=name, model=spec.local_path or inf.model, served_name=inf.served_name,
                host=inf.host, port=inf.port, tensor_parallel_size=plan.tensor_parallel_size,
                pipeline_parallel_size=inf.pipeline_parallel_size,
                data_parallel_size=plan.replicas, max_model_len=inf.context_length,
                gpu_memory_utilization=plan.gpu_memory_utilization or inf.gpu_memory_utilization,
                max_num_seqs=seqs, max_num_batched_tokens=batched,
                tool_call_parser=inf.tool_call_parser, reasoning_parser=inf.reasoning_parser,
                reasoning_mode=inf.reasoning_mode, dtype=inf.dtype,
                quantization=inf.quantization, revision=None, enforce_eager=eager,
                api_key_env=inf.api_key_env, extra_args=list(inf.extra_server_args),
                sampling_defaults={k: v for k, v in (("temperature", inf.temperature),
                                                     ("top_p", inf.top_p),
                                                     ("top_k", inf.top_k)) if v is not None},
            )
            cmd = sspec.command(binary)
            missing = validate_flags(cmd, help_text)
            if missing:
                result.error = (f"Installed vLLM ({binary}) does not support flag(s) {missing}; "
                                "refusing to guess. Adjust the config or vLLM version.")
                return result
            result.plan = plan.to_dict()
            if dry_run:
                result.ok = True
                result.attempts.append({"command": _redacted(cmd)})
                return result
            keys = [d["uuid"] or d["visible_token"] for d in plan.device_map]
            reservations.reserve(keys, label=f"{name}:{inf.model}")
            log_path = log_dir / f"{name}_{time.strftime('%Y%m%dT%H%M%S')}_a{attempt}.log"
            env = server_env(plan, binary, allow_download=inf.allow_model_download)
            handle = log_path.open("w")
            started = time.monotonic()
            proc = subprocess.Popen(cmd, env=env, stdout=handle, stderr=subprocess.STDOUT,
                                    start_new_session=True, cwd=str(log_dir))
            handle.close()
            reservations.update_pid(keys, proc.pid)
        connect_host = "127.0.0.1" if inf.host in {"0.0.0.0", "::"} else inf.host
        endpoint = f"http://{connect_host}:{inf.port}/v1"
        state = {
            "name": name, "pid": proc.pid, "pgid": proc.pid, "uid": os.getuid(),
            "host": socket.gethostname(), "endpoint": endpoint, "model": inf.model,
            "served_name": inf.served_name, "command": _redacted(cmd),
            "cuda_visible_devices": plan.cuda_visible_devices, "device_map": plan.device_map,
            "reservation_keys": keys, "log": str(log_path), "started_at": time.time(),
            "tensor_parallel_size": plan.tensor_parallel_size, "replicas": plan.replicas,
            "gpu_memory_utilization": sspec.gpu_memory_utilization,
            "max_model_len": sspec.max_model_len, "max_num_seqs": seqs,
            "vllm_version": vllm_version(binary), "model_spec": spec.to_dict(),
        }
        _write_state(name, state)
        attempt_info: dict[str, Any] = {"attempt": attempt, "command": _redacted(cmd),
                                        "log": str(log_path), "pid": proc.pid}
        status = _wait_ready(proc, endpoint, inf.served_name, inf.startup_timeout_s)
        attempt_info["status"] = status
        result.attempts.append(attempt_info)
        result.log_path = str(log_path)
        if status == "ready":
            result.ok, result.endpoint, result.pid = True, endpoint, proc.pid
            result.startup_s = round(time.monotonic() - started, 1)
            state["startup_s"] = result.startup_s
            _write_state(name, state)
            return result
        # failed: stop only what we started, keep the log, maybe retry smaller
        stop(name, quiet=True)
        failure = classify_failure(_tail(log_path, 200))
        attempt_info["failure"] = failure
        result.error = f"server failed to start ({status}/{failure}); log: {log_path}"
        if failure != "oom" or not cfg.gpu.allow_retry_smaller or attempt == max_retries:
            return result
        seqs = max(1, seqs // 2)
        batched = max(2048, (batched or 8192) // 2)
        eager = True
        policy.gpu_memory_utilization = max(0.1, round(policy.gpu_memory_utilization - 0.05, 2))
        log.warning("OOM during start-up; retrying with max_num_seqs=%d, "
                    "max_num_batched_tokens=%s, enforce_eager, gpu_memory_utilization<=%.2f",
                    seqs, batched, policy.gpu_memory_utilization)
    return result


def _wait_ready(proc: subprocess.Popen, endpoint: str, served: str, timeout: float) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return f"exited({proc.returncode})"
        if endpoint_ready(endpoint, served):
            return "ready"
        time.sleep(2.0)
    return "timeout"


# ------------------------------------------------------------------ state + stop


def _state_path(name: str) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in name)
    return state_dir() / f"{safe}.json"


def _write_state(name: str, state: dict[str, Any]) -> None:
    _state_path(name).write_text(json.dumps(state, indent=2))


def _load_state(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _proc_uid(pid: int) -> int | None:
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("Uid:"):
                return int(line.split()[1])
    except (OSError, ValueError):
        return None
    return None


def _cmdline(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        return raw.replace(b"\0", b" ").decode(errors="replace")
    except OSError:
        return ""


def _pid_is_ours(pid: Any) -> bool:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    return pid > 0 and _proc_uid(pid) == os.getuid()


def _group_members(pgid: int) -> list[int]:
    members = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text()
            fields = stat.rsplit(")", 1)[1].split()
            if int(fields[2]) == pgid and _proc_uid(int(entry.name)) == os.getuid():
                members.append(int(entry.name))
        except (OSError, ValueError, IndexError):
            continue
    return members


def list_servers() -> list[dict[str, Any]]:
    out = []
    for path in sorted(state_dir().glob("*.json")):
        if path.name.startswith("vllm_help"):
            continue
        state = _load_state(path)
        if not state:
            continue
        state["alive"] = _pid_is_ours(state.get("pid"))
        state["healthy"] = state["alive"] and endpoint_ready(state["endpoint"],
                                                              state["served_name"])
        out.append(state)
    return out


def stop(name: str, *, timeout: float = 60.0, quiet: bool = False) -> dict[str, Any]:
    """Stop a server we launched. Refuses anything not recorded in our state file."""
    path = _state_path(name)
    state = _load_state(path)
    if state is None:
        return {"ok": False, "error": f"no state file for server {name!r} in {state_dir()}"}
    pid = int(state.get("pid") or 0)
    pgid = int(state.get("pgid") or pid)
    report: dict[str, Any] = {"name": name, "pid": pid, "signalled": []}
    if state.get("uid") not in (None, os.getuid()):
        return {"ok": False, "error": "state file belongs to another user; refusing"}
    alive = _pid_is_ours(pid)
    if alive and "vllm" not in _cmdline(pid):
        return {"ok": False, "error": f"pid {pid} is not a vLLM process anymore; refusing"}
    members = _group_members(pgid) if pgid > 0 else []
    if alive or members:
        try:
            os.killpg(pgid, signal.SIGTERM)
            report["signalled"].append("SIGTERM")
        except ProcessLookupError:
            pass
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and _group_members(pgid):
            time.sleep(1.0)
        if _group_members(pgid):
            try:
                os.killpg(pgid, signal.SIGKILL)
                report["signalled"].append("SIGKILL")
            except ProcessLookupError:
                pass
            time.sleep(2.0)
    GpuReservations().release(list(state.get("reservation_keys") or []))
    history = state_dir() / "history"
    history.mkdir(exist_ok=True)
    state["stopped_at"] = time.time()
    (history / f"{path.stem}_{int(time.time())}.json").write_text(json.dumps(state, indent=2))
    path.unlink(missing_ok=True)
    report["ok"] = not _group_members(pgid)
    report["remaining_pids"] = _group_members(pgid)
    if not quiet:
        log.info("stopped server %s: %s", name, report)
    return report
