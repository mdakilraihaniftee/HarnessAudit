"""Per-GPU memory requirement estimates for serving a model with vLLM.

requirement(per GPU) = weights/TP + KV cache + runtime overhead + activations
                       (+ the placement safety margin, applied by the allocator)

The estimate is deliberately conservative and is checked against the *live*
free memory of each candidate GPU. Free memory is a snapshot, not a promise:
another user can allocate memory after we start, which is why the allocator
keeps a safety margin and the server launcher handles OOM.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from .model_registry import ModelSpec

GIB = 1024**3


@dataclass
class MemoryEstimate:
    tensor_parallel_size: int
    weights_gb: float            # per GPU
    kv_min_gb: float             # one full max_model_len sequence (vLLM start-up minimum)
    kv_target_gb: float          # what we want for concurrency
    runtime_overhead_gb: float   # CUDA context, cudagraphs, allocator slack
    activation_gb: float         # prefill workspace for max_num_batched_tokens
    required_min_gb: float       # weights + kv_min + overhead + activations
    required_target_gb: float    # weights + kv_target + overhead + activations
    kv_bytes_per_token: float    # per GPU
    max_model_len: int
    target_concurrent_tokens: int

    def to_dict(self) -> dict[str, float | int]:
        return {k: (round(v, 3) if isinstance(v, float) else v) for k, v in asdict(self).items()}

    def kv_tokens_for(self, kv_budget_gb: float) -> int:
        if self.kv_bytes_per_token <= 0:
            return 0
        return max(0, int(kv_budget_gb * GIB / self.kv_bytes_per_token))


def estimate(
    spec: ModelSpec,
    *,
    tensor_parallel_size: int,
    max_model_len: int,
    max_num_seqs: int,
    max_num_batched_tokens: int | None = None,
    kv_dtype_bytes: float = 2.0,
    target_full_sequences: float = 2.0,
    runtime_overhead_gb: float = 2.5,
) -> MemoryEstimate:
    tp = tensor_parallel_size
    if tp < 1:
        raise ValueError("tensor_parallel_size must be >= 1")
    # KV heads are split across TP ranks; when TP > kv heads they are replicated.
    kv_heads_per_gpu = max(1, math.ceil(spec.num_kv_heads / tp))
    kv_per_token = 2 * spec.num_layers * kv_heads_per_gpu * spec.head_dim * kv_dtype_bytes
    weights = spec.weights_gb / tp
    kv_min = max_model_len * kv_per_token / GIB
    # Agent workloads keep several long conversations alive at once; plan for
    # `target_full_sequences` full-length sequences (bounded by max_num_seqs).
    target_tokens = int(max_model_len * min(float(max_num_seqs), target_full_sequences))
    kv_target = target_tokens * kv_per_token / GIB
    batched = max_num_batched_tokens or max(max_model_len, 8192)
    hidden = spec.num_attention_heads * spec.head_dim
    # Rough prefill workspace: a few fp32/bf16 buffers of [batched_tokens x hidden].
    activation = batched * hidden * 2 * 8 / GIB / tp
    return MemoryEstimate(
        tensor_parallel_size=tp,
        weights_gb=weights,
        kv_min_gb=kv_min,
        kv_target_gb=kv_target,
        runtime_overhead_gb=runtime_overhead_gb,
        activation_gb=activation,
        required_min_gb=weights + kv_min + runtime_overhead_gb + activation,
        required_target_gb=weights + kv_target + runtime_overhead_gb + activation,
        kv_bytes_per_token=kv_per_token,
        max_model_len=max_model_len,
        target_concurrent_tokens=target_tokens,
    )
