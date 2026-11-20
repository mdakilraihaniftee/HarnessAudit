# Slurm cluster guide (A100 / GH200)

> **Validation status.** No Slurm cluster was reachable from the development
> machine. The sbatch scripts were checked with `bash -n`, `submit_campaign.sh
> --dry-run`, and by running `slurm_qwen_single.sbatch` end-to-end on a 4×L40S
> workstation under a **simulated** Slurm environment (`SLURM_JOB_ID`,
> `CUDA_VISIBLE_DEVICES=1`, …): the server used only the "granted" GPU 1, all
> checks passed and the GPU was released. Real multi-node runs, Ray, NCCL and
> site partitions are **pending live validation**.

## One-time setup per cluster (login node, per architecture)

`$HOME` is usually shared between the x86_64 (A100) and aarch64 (GH200)
clusters, so every environment is arch-specific:

```bash
# HarnessAudit env (rc kit):  ~/venvs/HarnessAudit-<sporc|tigris>
rc/setup.sh
# vLLM + Qwen Code + weights: ~/venvs/HarnessAudit-vllm-<arch>,
#                             ~/.local/share/harnessaudit/qwen-code-<arch>
source rc/common.sh            # sets the shared HF_HUB_CACHE for all projects
scripts/local_qwen/03_setup_qwen.sh --allow-download
# only for one model spanning several nodes:
scripts/local_qwen/03_setup_qwen.sh --allow-download --only ray
```

vLLM 0.33.0 publishes `manylinux_2_28_aarch64` wheels, and `vllm download-kernels`
installs FlashInfer kernels for sm80 (A100), sm89 (L40S) and sm90a (GH200), so
no CUDA toolkit is needed on compute nodes. Compute nodes need no internet: the
server runs with `HF_HUB_OFFLINE=1`.

## Profiles

`configs/cluster/*.yaml` hold defaults; flags override them. Partition and
account values are this repository's rc-kit values (`rc/common.sh`:
`sporc-gpu`/`tigris`, account `defake`). For another site, pass `--partition`
and `--account` — nothing is invented.

| Profile | Intended hardware | Notes |
|---|---|---|
| `l40_shared` | shared workstation | auto selection, sharing off, 6 GB margin |
| `a100_single` | one A100 node | scheduler selection, `gpu:a100:1`, 0.90 utilization; 40 vs 80 GB detected at run time |
| `a100_multinode` | 2–6 A100 nodes | `gpu:a100:4` per node, replicas `auto` |
| `gh200` | one GH200 node | `gpu:gh200:1`, aarch64 env; HBM size detected at run time |

## Nodes, node names, GPUs — kept separate

`submit_campaign.sh` never derives one from another:

* `--nodes N` — node count (`--nodes 1|2|4|6`)
* `--nodelist a,b` — specific node names (if `--nodes` is omitted it equals the
  number of names; more names than `--nodes` is an error; fewer prints a note that
  Slurm will add nodes)
* `--gpus-per-node G` / `--gpu-type T` / `--gres STR` — GPUs on each node
* inside the job: `CUDA_VISIBLE_DEVICES` / `SLURM_STEP_GPUS` decide which GPUs are
  used; if a 4-GPU node grants 1 GPU, only that GPU is used.

## Scripts

| Script | Mode |
|---|---|
| `scripts/hpc/slurm_qwen_single.sbatch` | node-local server; health, tool calling, cost check; then `HA_JOB_CMD` or serve for `SERVE_SECONDS` |
| `scripts/hpc/slurm_finance_campaign.sbatch` | node-local server + SI-ATG campaign (`MODE=campaign`) or clean runs (`MODE=clean`); job arrays give independent campaigns |
| `scripts/hpc/slurm_parallel_evaluation.sbatch` | **distributed experiment evaluation**: `srun` one task per node; each node runs its own server on its own GPUs (data-parallel replicas) and its own shard of trials. No cross-node traffic |
| `scripts/hpc/slurm_qwen_multinode.sbatch` | **distributed inference**: one model across nodes, TP = GPUs per node, PP = nodes, vLLM Ray executor; Ray started inside the allocation with `srun` |
| `scripts/hpc/slurm_a100_example.sbatch`, `slurm_gh200_example.sbatch` | one node: inference + tool tests + cost check + ClawTeam integration test + clean runs |
| `scripts/hpc/submit_campaign.sh` | builds the `sbatch` command from a profile; `--dry-run` prints it |

Examples (dry-run output shown is real):

```bash
scripts/hpc/submit_campaign.sh --profile a100_single --dry-run
# sbatch --nodes=1 --partition=sporc-gpu --account=defake --time=08:00:00 --cpus-per-task=16
#        --mem=64G --gres=gpu:a100:1 --export=ALL,PROFILE=a100_single scripts/hpc/slurm_finance_campaign.sbatch

scripts/hpc/submit_campaign.sh --profile a100_multinode --nodes 4 \
    --script slurm_parallel_evaluation.sbatch --mode clean --repeat 8
scripts/hpc/submit_campaign.sh --profile a100_multinode --nodelist gpu03,gpu07   # names → 2 nodes
scripts/hpc/submit_campaign.sh --profile gh200 --script slurm_gh200_example.sbatch
scripts/hpc/submit_campaign.sh --profile a100_single --array 0-3 --rounds 10     # 4 campaigns
```

## Which multi-node mode?

For Qwen3-8B (or 14B) on A100/GH200, use **distributed experiment evaluation**:
the model fits on one GPU, and independent trials parallelise perfectly across
nodes with zero inter-node traffic. Use **distributed inference** only for models
that do not fit on one node; one TP group should not span different GPU types,
so put heterogeneous nodes (A100 vs GH200) in separate jobs/servers.

## Security

* Model servers bind `127.0.0.1`. In the multi-node script the API server also
  binds loopback on the head node because the clients run there; only Ray/NCCL
  traffic crosses nodes, inside the allocation (dashboard disabled, usage stats
  off). Pin NCCL/Gloo to the cluster fabric with `NETWORK_INTERFACE=ib0`.
* If clients on other nodes must reach a server, bind it to the cluster interface,
  set `inference.api_key_env` (vLLM `--api-key`), and add the host to
  `inference.approved_endpoints` — the cost guard refuses unapproved non-loopback
  endpoints.
* Never expose a Qwen Code daemon (`qwen serve`) or any shell-capable agent
  runtime; only the model endpoint is a network service.
* No SSH, no administrator privileges, no services outside the job: everything
  starts via `srun` and is stopped by the job's `EXIT` trap.
