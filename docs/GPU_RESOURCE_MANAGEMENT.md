# GPU resource management

`multi_agent/local_inference/` chooses GPUs **before** a model loads, reserves
them against this project's own concurrent launches, and never touches other
users' processes. Nothing about GPU models or memory sizes is hard-coded: every
value comes from NVML (ctypes binding to `libnvidia-ml`, falling back to
`nvidia-smi`), so the same code runs on L40/L40S, A100 40/80 GB and GH200.

```bash
python -m multi_agent.local_inference gpu-status            # node view + which GPUs we may use
python -m multi_agent.local_inference gpu-status --json
python -m multi_agent.local_inference plan                  # dry-run placement for the configured model
python -m multi_agent.local_inference plan --gpu-ids 0,2,3 --replicas auto
python -m multi_agent.local_inference serve [--gpu auto|scheduler | --gpu-ids 0,2,3]
python -m multi_agent.local_inference status | stop --name qwen-8000
```

## Which GPUs are permitted

1. An inherited `CUDA_VISIBLE_DEVICES` always restricts the set (indices, `GPU-…`
   UUIDs and `MIG-…` UUIDs are understood; inside a cgroup that renumbers devices
   the tokens are matched by position).
2. In a Slurm job (`SLURM_JOB_ID` set) or with `--gpu scheduler`: only
   `SLURM_STEP_GPUS`/`SLURM_JOB_GPUS` (when `CUDA_VISIBLE_DEVICES` is unset). A job
   without GPUs fails closed — there is no CPU fallback.
3. Otherwise all NVML devices are candidates, subject to the policy below.

The child server receives the exact permitted tokens in `CUDA_VISIBLE_DEVICES`
with `CUDA_DEVICE_ORDER=PCI_BUS_ID`, so `cuda:i` inside vLLM maps to the
`nvidia-smi` index printed in the plan:

```
PLAN: TP=1 x replicas=3  CUDA_VISIBLE_DEVICES=0,2,3  gpu_memory_utilization=0.80
  cuda:0 (replica 0) -> nvidia-smi GPU 0 GPU-ec5f…
  cuda:1 (replica 1) -> nvidia-smi GPU 2 GPU-e7b4…
  cuda:2 (replica 2) -> nvidia-smi GPU 3 GPU-b5bb…
```

## Selection modes

| Mode | Behaviour |
|---|---|
| `auto` (default) | rank eligible GPUs: idle first (`prefer_idle_gpu`), then most usable memory, lowest utilization, lowest index; one replica unless asked for more |
| `explicit` (`--gpu-ids 0,2,3`, `HA_GPU_IDS`) | exactly those `nvidia-smi` indices; an id outside the permitted set is an error, never a substitution; GPU 1 is never touched in this example. `--replicas auto` uses all listed GPUs as data-parallel replicas |
| `scheduler` | only Slurm-granted GPUs; `--replicas auto` uses all of them |

## Eligibility policy

| Setting (`gpu.*`) | Default | Effect |
|---|---|---|
| `allow_shared_gpu` | `false` | a GPU with other users' processes, or more than 1 GB used by others, is *occupied* and skipped unless this is true |
| `max_gpu_utilization` | 30 % | GPUs busier than this are never selected (even when sharing is allowed) |
| `memory_safety_margin_gb` | 6 | free memory left untouched for other users' growth |
| `min_free_memory_gb` | 0 | hard floor |
| `prefer_idle_gpu` | `true` | idle GPUs first |
| MIG | — | MIG-enabled GPUs are not auto-selected; a MIG instance passed via `CUDA_VISIBLE_DEVICES` fails closed because its memory cannot be verified through this NVML path |

## Memory estimate

Per GPU, for tensor-parallel size `TP`:

```
weights/TP  +  KV(min: one full max_model_len sequence)  +  runtime overhead (2.5 GB)
            +  prefill activations(max_num_batched_tokens)        ≤  budget
budget = min(gpu_memory_utilization × total, free − safety margin)
```

Weights come from the cached safetensors sizes (exact) or the registry; KV bytes
per token = `2 × layers × ceil(kv_heads/TP) × head_dim × 2`. For Qwen3-8B that is
144 KiB/token, i.e. 4.5 GiB for one 32k sequence. The estimate is conservative:
on an L40S at `gpu_memory_utilization=0.80`, vLLM reported 20.0 GiB of KV cache
(145,600 tokens, 4.44 concurrent 32k sequences) where the planner predicted
≈129k tokens. vLLM receives `gpu_memory_utilization = budget/total`, so on a
partially used GPU it never asks for more than (free − margin).

Free memory is a snapshot, not a guarantee: another user can allocate memory
after we start. That is what the margin is for; if it is not enough, vLLM fails
with OOM and the launcher handles it (below).

## Tensor vs data parallelism

* `tensor_parallel_size: auto` picks the **smallest** valid TP that fits. Valid
  sizes must divide the attention heads and be compatible with the KV heads
  (Qwen3-8B: 1, 2, 4, 8 — never 3). Qwen3-8B fits on one L40S, so `auto` → TP=1.
* Extra GPUs become **data-parallel replicas** (vLLM `--data-parallel-size`
  behind one endpoint), never an invalid or wasteful TP group. Three listed GPUs
  with an 8B model → 3 replicas; with a 14B model on 32 GB cards → TP=2 and the
  third GPU is reported as unused, with the reason.
* Replicas are always homogeneous TP groups (same GPU model inside a group).
* Measured trade-offs are in `docs/LOCAL_PERFORMANCE_REPORT.md`.

## Reservations (our own launches only)

`GpuReservations` writes one `O_EXCL` lock file per GPU UUID under
`~/.cache/harnessaudit/gpu_locks/<host>/` (override `HA_GPU_LOCK_DIR`), and
serialises plan+reserve with an `flock`, so two HarnessAudit launches on the same
node cannot pick the same GPU (tested with a two-thread race). Locks from dead
processes are reclaimed. These locks do **not** coordinate with other users'
software.

## OOM handling and shutdown

* Start-up failures are classified from the server log (OOM patterns, port in
  use, model not cached). On OOM, if `allow_retry_smaller` is true, the launcher
  stops **its own** process group, keeps the log, and retries up to twice with
  halved `max_num_seqs` / `max_num_batched_tokens`, `--enforce-eager`, and a
  5-point lower utilization (tested with a fake vLLM that always OOMs).
* `stop` signals only the process group recorded in our state file, after
  checking that the PID belongs to this user and is a vLLM process; anything
  else is refused. Reservations are released and the state is moved to
  `history/`. Verified: GPU memory returns to 0 MiB after `stop`.

## Live migration

Not supported, and not claimed. A loaded vLLM model cannot move between GPUs; to
change placement, stop the server and start it again (requests queue in the
clients' retry logic meanwhile). If another user starts using "our" GPU, we do
not touch their job: `gpu-status` shows the contention, and you can stop our
server and re-plan.
