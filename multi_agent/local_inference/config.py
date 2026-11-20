"""Central configuration for local inference, GPU placement and cluster runs.

Resolution order (later wins):

1. Built-in defaults (fully local, ``inference.mode = local_only``).
2. The YAML file ``configs/local_qwen.yaml`` (or ``HA_CONFIG`` / ``--config``).
3. An optional cluster profile overlay (``HA_CLUSTER_PROFILE`` / ``--profile``),
   e.g. ``configs/cluster/l40_shared.yaml``.
4. Dedicated environment variables (``HA_INFERENCE_MODE``, ``HA_LOCAL_ENDPOINT``,
   ``HA_MODEL``, ``HA_GPU_IDS``, ...).
5. Generic dotted overrides from ``HA_SET="a.b=1,c.d=x"``.
6. Command-line overrides (``--set a.b=1`` and dedicated flags).

The effective configuration is recorded with every experiment (see
:func:`effective_snapshot`) so local-Qwen results stay distinguishable from
hosted-model results.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "configs" / "local_qwen.yaml"
CLUSTER_PROFILE_DIR = REPO_ROOT / "configs" / "cluster"

InferenceMode = Literal["local_only", "paid_opt_in"]
ROLES: tuple[str, ...] = (
    "attacker_generator",
    "attacker_reflector",
    "attacker_harness_optimizer",
    "target_coordinator",
    "target_specialists",
    "completion_judge",
    "operational_judge",
    "perturbation_judge",
)
ROLE_GROUPS: dict[str, tuple[str, ...]] = {
    "attacker": ("attacker_generator", "attacker_reflector", "attacker_harness_optimizer"),
    "target": ("target_coordinator", "target_specialists"),
    "judge": ("completion_judge", "operational_judge", "perturbation_judge"),
}


def _reasoning_mode(value: Any) -> Any:
    """Accept YAML/env booleans for reasoning_mode (``on``/``off`` parse as bools)."""
    if isinstance(value, bool):
        return "on" if value else "off"
    return value


class InferenceSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: InferenceMode = "local_only"
    backend: Literal["vllm", "sglang", "ollama", "openai_compatible"] = "vllm"
    model: str = "Qwen/Qwen3-8B"
    model_revision: str | None = None
    served_model_name: str | None = None
    endpoint: str = "http://127.0.0.1:8000/v1"
    api_key_env: str | None = None
    approved_endpoints: list[str] = Field(default_factory=list)
    allow_paid_fallback: bool = False
    allow_model_download: bool = False
    context_length: int = 32768
    max_output_tokens: int = 4096
    # Qwen3 model-card recommendation for non-thinking mode (judges override to 0.0).
    temperature: float = 0.7
    top_p: float | None = 0.8
    top_k: int | None = 20
    reasoning_mode: Literal["off", "on"] = "off"
    tool_call_parser: str = "hermes"
    reasoning_parser: str | None = "qwen3"
    tensor_parallel_size: int | Literal["auto"] = "auto"
    pipeline_parallel_size: int = 1
    data_parallel_replicas: int | Literal["auto"] = 1
    gpu_memory_utilization: float = 0.80
    max_num_seqs: int = 16
    max_num_batched_tokens: int | None = 8192
    dtype: str = "auto"
    quantization: str | None = None
    host: str = "127.0.0.1"
    port: int = 8000
    request_timeout_s: float = 600.0
    startup_timeout_s: float = 900.0
    extra_server_args: list[str] = Field(default_factory=list)

    _rm = field_validator("reasoning_mode", mode="before")(classmethod(
        lambda cls, v: _reasoning_mode(v)))

    @field_validator("gpu_memory_utilization")
    @classmethod
    def _gmu(cls, value: float) -> float:
        if not 0.05 <= value <= 0.98:
            raise ValueError("gpu_memory_utilization must be between 0.05 and 0.98")
        return value

    @model_validator(mode="after")
    def _bounds(self) -> InferenceSettings:
        if self.context_length < 1024:
            raise ValueError("context_length must be >= 1024")
        if self.max_output_tokens < 1 or self.max_output_tokens >= self.context_length:
            raise ValueError("max_output_tokens must be positive and below context_length")
        if self.max_num_seqs < 1 or self.pipeline_parallel_size < 1:
            raise ValueError("max_num_seqs and pipeline_parallel_size must be positive")
        if isinstance(self.data_parallel_replicas, int) and self.data_parallel_replicas < 1:
            raise ValueError("data_parallel_replicas must be positive or 'auto'")
        if isinstance(self.tensor_parallel_size, int) and self.tensor_parallel_size < 1:
            raise ValueError("tensor_parallel_size must be positive or 'auto'")
        return self

    @property
    def served_name(self) -> str:
        return self.served_model_name or self.model


class RouteSettings(BaseModel):
    """Per-role overrides. Unset fields inherit from ``inference``."""

    model_config = ConfigDict(extra="forbid")

    model: str | None = None
    endpoint: str | None = None
    api_key_env: str | None = None
    temperature: float | None = None
    max_output_tokens: int | None = None
    reasoning_mode: Literal["off", "on"] | None = None
    enabled: bool = True

    _rm = field_validator("reasoning_mode", mode="before")(classmethod(
        lambda cls, v: _reasoning_mode(v)))


class QwenCodeSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bin: str | None = None
    # Hosted search/fetch tools are removed so agents cannot reach external services.
    exclude_tools: list[str] = Field(
        default_factory=lambda: ["web_search", "web_fetch"]
    )
    max_session_turns: int = 80
    attacker_timeout_s: int = 300
    attacker_retries: int = 2


class HarnessSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default: str = "qwen_code"
    preserve_existing_harnesses: bool = True
    qwen_code: QwenCodeSettings = Field(default_factory=QwenCodeSettings)


class GpuSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selection: Literal["auto", "explicit", "scheduler"] = "auto"
    explicit_ids: list[int] | None = None
    allow_shared_gpu: bool = False
    prefer_idle_gpu: bool = True
    min_free_memory_gb: float = 0.0
    max_gpu_utilization: float = 30.0
    memory_safety_margin_gb: float = 6.0
    lock_dir: str | None = None
    allow_retry_smaller: bool = True

    @field_validator("explicit_ids", mode="before")
    @classmethod
    def _parse_ids(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return None
            return [int(item) for item in value.split(",") if item.strip()]
        if isinstance(value, int):
            return [value]
        return value


class ParallelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_parallel_trials: int = 1
    max_agent_workers: int = 8
    max_judge_workers: int = 1

    @model_validator(mode="after")
    def _positive(self) -> ParallelSettings:
        if min(self.max_parallel_trials, self.max_agent_workers, self.max_judge_workers) < 1:
            raise ValueError("parallel worker limits must be positive")
        return self


class ClusterSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scheduler: Literal["auto", "slurm", "none"] = "auto"
    nodes: int = 1
    nodelist: list[str] | None = None
    exclude: list[str] | None = None
    partition: str | None = None
    account: str | None = None
    gpus_per_node: int = 1
    gpu_type: str | None = None
    cpus_per_task: int | None = None
    mem: str | None = None
    time: str | None = None
    network_interface: str | None = None

    @field_validator("nodelist", "exclude", mode="before")
    @classmethod
    def _parse_list(cls, value: Any) -> Any:
        if isinstance(value, str):
            items = [item.strip() for item in value.split(",") if item.strip()]
            return items or None
        return value


class ExperimentSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: str = "finance"
    task: str = "fin-t1"
    default_dry_run: bool = True
    skip_judge: bool = True


class LocalInferenceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inference: InferenceSettings = Field(default_factory=InferenceSettings)
    routes: dict[str, RouteSettings] = Field(default_factory=dict)
    harness: HarnessSettings = Field(default_factory=HarnessSettings)
    gpu: GpuSettings = Field(default_factory=GpuSettings)
    parallel: ParallelSettings = Field(default_factory=ParallelSettings)
    cluster: ClusterSettings = Field(default_factory=ClusterSettings)
    experiment: ExperimentSettings = Field(default_factory=ExperimentSettings)
    # Provenance of the effective config (not user-settable).
    sources: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _known_roles(self) -> LocalInferenceConfig:
        unknown = sorted(set(self.routes) - set(ROLES))
        if unknown:
            raise ValueError(f"Unknown route role(s): {unknown}. Known: {list(ROLES)}")
        return self

    def route(self, role: str) -> RouteSettings:
        if role not in ROLES:
            raise ValueError(f"Unknown model role {role!r}; known roles: {list(ROLES)}")
        return self.routes.get(role) or RouteSettings()


# ----------------------------------------------------------------- merging helpers


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in (overlay or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _coerce_scalar(raw: str) -> Any:
    text = raw.strip()
    lowered = text.lower()
    if lowered in {"true", "yes", "on"}:
        return True
    if lowered in {"false", "no"}:
        return False
    if lowered in {"null", "none", "~"}:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return text


def _set_dotted(data: dict[str, Any], dotted: str, value: Any) -> None:
    parts = [part for part in dotted.strip().split(".") if part]
    if not parts:
        raise ValueError(f"Invalid override key: {dotted!r}")
    cursor = data
    for part in parts[:-1]:
        nxt = cursor.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cursor[part] = nxt
        cursor = nxt
    cursor[parts[-1]] = value


def parse_overrides(items: list[str] | str | None) -> dict[str, Any]:
    """Parse ``a.b=1`` overrides (list or comma-separated string) into a nested dict."""
    if not items:
        return {}
    if isinstance(items, str):
        items = [chunk for chunk in items.split(",") if chunk.strip()]
    out: dict[str, Any] = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"Override must look like key=value, got {item!r}")
        key, raw = item.split("=", 1)
        _set_dotted(out, key, _coerce_scalar(raw))
    return out


def _models_shorthand(data: dict[str, Any]) -> dict[str, Any]:
    """Expand ``models: {attacker: X, target: Y, judge: Z}`` into per-role routes."""
    models = data.pop("models", None)
    if not models:
        return data
    routes = data.setdefault("routes", {})
    for group, model in models.items():
        if model is None:
            continue
        roles = ROLE_GROUPS.get(group, (group,) if group in ROLES else ())
        if not roles:
            raise ValueError(f"Unknown models group {group!r}; use attacker, target, judge")
        for role in roles:
            routes.setdefault(role, {})
            routes[role].setdefault("model", model)
    return data


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return _models_shorthand(data)


def resolve_profile_path(profile: str | None) -> Path | None:
    if not profile:
        return None
    candidate = Path(profile)
    if candidate.is_file():
        return candidate
    named = CLUSTER_PROFILE_DIR / f"{profile}.yaml"
    if named.is_file():
        return named
    raise FileNotFoundError(
        f"Cluster profile {profile!r} not found (looked for {candidate} and {named})"
    )


_ENV_MAP: dict[str, str] = {
    "HA_INFERENCE_MODE": "inference.mode",
    "HA_LOCAL_ENDPOINT": "inference.endpoint",
    "HA_MODEL": "inference.model",
    "HA_MODEL_REVISION": "inference.model_revision",
    "HA_INFERENCE_BACKEND": "inference.backend",
    "HA_MAX_MODEL_LEN": "inference.context_length",
    "HA_TENSOR_PARALLEL_SIZE": "inference.tensor_parallel_size",
    "HA_GPU_MEMORY_UTILIZATION": "inference.gpu_memory_utilization",
    "HA_MAX_NUM_SEQS": "inference.max_num_seqs",
    "HA_SERVER_PORT": "inference.port",
    "HA_ALLOW_MODEL_DOWNLOAD": "inference.allow_model_download",
    "HA_GPU_SELECTION": "gpu.selection",
    "HA_GPU_IDS": "gpu.explicit_ids",
    "HA_ALLOW_SHARED_GPU": "gpu.allow_shared_gpu",
    "HA_GPU_SAFETY_MARGIN_GB": "gpu.memory_safety_margin_gb",
    "HA_MAX_GPU_UTILIZATION": "gpu.max_gpu_utilization",
    "HA_QWEN_BIN": "harness.qwen_code.bin",
}


def _env_overrides(env: dict[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for var, dotted in _ENV_MAP.items():
        value = env.get(var)
        if value is None or value == "":
            continue
        if dotted == "gpu.explicit_ids":
            _set_dotted(out, dotted, value)
        elif dotted in {"inference.model", "inference.endpoint", "harness.qwen_code.bin",
                        "inference.model_revision"}:
            _set_dotted(out, dotted, value)
        else:
            _set_dotted(out, dotted, _coerce_scalar(value))
    # HA_GPU_IDS implies explicit selection unless a selection mode was given.
    if env.get("HA_GPU_IDS") and not env.get("HA_GPU_SELECTION"):
        _set_dotted(out, "gpu.selection", "explicit")
    for role in ROLES:
        prefix = f"HA_ROUTE_{role.upper()}_"
        for field in ("MODEL", "ENDPOINT"):
            value = env.get(prefix + field)
            if value:
                _set_dotted(out, f"routes.{role}.{field.lower()}", value)
    return out


def load_config(
    path: str | Path | None = None,
    *,
    profile: str | None = None,
    overrides: list[str] | dict[str, Any] | None = None,
    env: dict[str, str] | None = None,
) -> LocalInferenceConfig:
    """Load the effective configuration with documented precedence."""
    env_map = dict(os.environ if env is None else env)
    sources = ["defaults"]
    data: dict[str, Any] = {}

    config_path = Path(path) if path else Path(env_map.get("HA_CONFIG") or DEFAULT_CONFIG_PATH)
    if config_path.is_file():
        data = _deep_merge(data, _load_yaml(config_path))
        sources.append(f"file:{config_path}")
    elif path or env_map.get("HA_CONFIG"):
        raise FileNotFoundError(f"Config file not found: {config_path}")

    profile_path = resolve_profile_path(profile or env_map.get("HA_CLUSTER_PROFILE"))
    if profile_path is not None:
        data = _deep_merge(data, _load_yaml(profile_path))
        sources.append(f"profile:{profile_path}")

    env_data = _env_overrides(env_map)
    if env_data:
        data = _deep_merge(data, env_data)
        sources.append("env:" + ",".join(sorted(k for k in _ENV_MAP if env_map.get(k))))

    generic = parse_overrides(env_map.get("HA_SET"))
    if generic:
        data = _deep_merge(data, generic)
        sources.append("env:HA_SET")

    if overrides:
        cli = overrides if isinstance(overrides, dict) else parse_overrides(list(overrides))
        cli = _models_shorthand(copy.deepcopy(cli))
        data = _deep_merge(data, cli)
        sources.append("cli")

    data.pop("sources", None)
    config = LocalInferenceConfig.model_validate(data)
    config.sources = sources
    return config


def effective_snapshot(config: LocalInferenceConfig) -> dict[str, Any]:
    """JSON-safe snapshot of the effective config (no secret values are stored)."""
    snap = config.model_dump(mode="json")
    return snap


def get_dotted(config: LocalInferenceConfig, dotted: str) -> Any:
    cursor: Any = config.model_dump(mode="json")
    for part in dotted.split("."):
        if isinstance(cursor, dict) and part in cursor:
            cursor = cursor[part]
        else:
            raise KeyError(dotted)
    return cursor


_CACHED: LocalInferenceConfig | None = None
_CACHED_KEY: tuple[Any, ...] | None = None


def current_config() -> LocalInferenceConfig:
    """Process-wide config, re-resolved when relevant environment variables change."""
    global _CACHED, _CACHED_KEY
    keys = ["HA_CONFIG", "HA_CLUSTER_PROFILE", "HA_SET", *_ENV_MAP]
    keys += [f"HA_ROUTE_{r.upper()}_{f}" for r in ROLES for f in ("MODEL", "ENDPOINT")]
    key = tuple(os.environ.get(k) for k in keys)
    if _CACHED is None or key != _CACHED_KEY:
        _CACHED = load_config()
        _CACHED_KEY = key
    return _CACHED


def inference_mode() -> str:
    """The active inference mode; ``local_only`` unless explicitly opted out."""
    return current_config().inference.mode
