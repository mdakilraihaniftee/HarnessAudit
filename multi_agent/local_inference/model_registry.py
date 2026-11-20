"""Known open-weight models and their architecture facts.

Entries are a fallback for planning before weights are downloaded. When the
model is in the local Hugging Face cache, :func:`model_spec` reads the real
``config.json`` and safetensors sizes instead, so estimates track the actual
checkpoint (and any quantized variant).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

GIB = 1024**3


@dataclass(frozen=True)
class ModelSpec:
    model_id: str
    params_b: float                 # total parameters (billions)
    num_layers: int
    num_attention_heads: int
    num_kv_heads: int
    head_dim: int
    native_context: int
    license: str
    tool_call_parser: str = "hermes"
    reasoning_parser: str | None = "qwen3"
    min_vllm: str = "0.8.5"
    min_transformers: str = "4.51.0"
    weight_dtype_bytes: float = 2.0
    weights_gb_exact: float | None = None   # from safetensors on disk, if known
    moe: bool = False
    source: str = "registry"
    revision: str | None = None
    local_path: str | None = None

    @property
    def weights_gb(self) -> float:
        if self.weights_gb_exact is not None:
            return self.weights_gb_exact
        return self.params_b * 1e9 * self.weight_dtype_bytes / GIB

    def kv_bytes_per_token(self, kv_dtype_bytes: float = 2.0) -> float:
        return 2 * self.num_layers * self.num_kv_heads * self.head_dim * kv_dtype_bytes

    def valid_tp_sizes(self, max_tp: int = 8) -> list[int]:
        sizes = []
        for tp in range(1, max_tp + 1):
            if self.num_attention_heads % tp:
                continue
            if self.num_kv_heads % tp and tp % self.num_kv_heads:
                continue
            sizes.append(tp)
        return sizes

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["weights_gb"] = round(self.weights_gb, 2)
        data["kv_bytes_per_token_bf16"] = self.kv_bytes_per_token()
        return data


# Architecture values from each model's config.json (Qwen3 family, Apache-2.0).
REGISTRY: dict[str, ModelSpec] = {
    spec.model_id: spec
    for spec in (
        ModelSpec("Qwen/Qwen3-0.6B", 0.6, 28, 16, 8, 128, 32768, "apache-2.0"),
        ModelSpec("Qwen/Qwen3-1.7B", 1.7, 28, 16, 8, 128, 32768, "apache-2.0"),
        ModelSpec("Qwen/Qwen3-4B", 4.0, 36, 32, 8, 128, 32768, "apache-2.0"),
        ModelSpec("Qwen/Qwen3-8B", 8.19, 36, 32, 8, 128, 32768, "apache-2.0"),
        ModelSpec("Qwen/Qwen3-14B", 14.8, 40, 40, 8, 128, 32768, "apache-2.0"),
        ModelSpec("Qwen/Qwen3-32B", 32.8, 64, 64, 8, 128, 32768, "apache-2.0"),
        ModelSpec("Qwen/Qwen3-30B-A3B", 30.5, 48, 32, 4, 128, 32768, "apache-2.0", moe=True),
        ModelSpec("Qwen/Qwen3-8B-FP8", 8.19, 36, 32, 8, 128, 32768, "apache-2.0",
                  weight_dtype_bytes=1.0),
        ModelSpec("Qwen/Qwen3-14B-FP8", 14.8, 40, 40, 8, 128, 32768, "apache-2.0",
                  weight_dtype_bytes=1.0),
        ModelSpec("Qwen/Qwen3-30B-A3B-FP8", 30.5, 48, 32, 4, 128, 32768, "apache-2.0",
                  weight_dtype_bytes=1.0, moe=True),
    )
}


def hf_cache_dir() -> Path:
    if os.environ.get("HF_HUB_CACHE"):
        return Path(os.environ["HF_HUB_CACHE"])
    if os.environ.get("HF_HOME"):
        return Path(os.environ["HF_HOME"]) / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


def local_snapshot(model_id: str, revision: str | None = None) -> Path | None:
    """Return the cached snapshot dir for ``model_id`` (no network access)."""
    if Path(model_id).is_dir():
        return Path(model_id)
    repo = hf_cache_dir() / ("models--" + model_id.replace("/", "--"))
    snaps = repo / "snapshots"
    if not snaps.is_dir():
        return None
    if revision:
        ref = repo / "refs" / revision
        rev = ref.read_text().strip() if ref.is_file() else revision
        cand = snaps / rev
        return cand if cand.is_dir() else None
    main = repo / "refs" / "main"
    if main.is_file():
        cand = snaps / main.read_text().strip()
        if cand.is_dir():
            return cand
    found = sorted(snaps.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    return found[0] if found else None


def model_spec(model_id: str, revision: str | None = None) -> ModelSpec | None:
    """Best available spec: local config.json > registry > None (unknown)."""
    base = REGISTRY.get(model_id)
    snap = local_snapshot(model_id, revision)
    if snap is None:
        return base
    cfg_path = snap / "config.json"
    if not cfg_path.is_file():
        return base
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    text_cfg = cfg.get("text_config", cfg)
    heads = int(text_cfg.get("num_attention_heads", base.num_attention_heads if base else 0))
    hidden = int(text_cfg.get("hidden_size", 0))
    head_dim = int(text_cfg.get("head_dim") or (hidden // heads if heads else 0))
    files = list(snap.glob("*.safetensors"))
    weights_exact = sum(f.stat().st_size for f in files) / GIB if files else None
    quant = cfg.get("quantization_config") or {}
    dtype_bytes = 1.0 if quant else 2.0
    spec = ModelSpec(
        model_id=model_id,
        params_b=base.params_b if base else ((weights_exact or 0) * GIB / dtype_bytes / 1e9),
        num_layers=int(text_cfg["num_hidden_layers"]),
        num_attention_heads=heads,
        num_kv_heads=int(text_cfg.get("num_key_value_heads", heads)),
        head_dim=head_dim,
        native_context=(base.native_context if base else
                        int(text_cfg.get("max_position_embeddings", 32768))),
        license=base.license if base else "unknown (check model card)",
        tool_call_parser=base.tool_call_parser if base else "hermes",
        reasoning_parser=base.reasoning_parser if base else None,
        weight_dtype_bytes=dtype_bytes,
        weights_gb_exact=weights_exact,
        moe=bool(text_cfg.get("num_experts")) or (base.moe if base else False),
        source="local_config",
        revision=snap.name,
        local_path=str(snap),
    )
    return spec


def with_quantization(spec: ModelSpec, quantization: str | None) -> ModelSpec:
    if not quantization or spec.weights_gb_exact is not None:
        return spec
    bytes_per = {"fp8": 1.0, "awq": 0.55, "gptq": 0.55, "bitsandbytes": 0.55}.get(
        quantization.lower(), spec.weight_dtype_bytes
    )
    return replace(spec, weight_dtype_bytes=bytes_per)
