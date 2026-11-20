# Local Qwen setup and runbook

Fully local HarnessAudit + SI-ATG: Qwen3-8B served by vLLM, ClawTeam agents and
the SI-ATG attacker running in the Qwen Code CLI, judges on the same server.
`inference.mode: local_only` is the default; nothing here contacts a paid API.

## 0. Requirements

* NVIDIA GPU(s) with ≥ 24 GB free for Qwen3-8B at 32k context (tested: L40S 46 GB)
* Node.js ≥ 20 for Qwen Code (tested 24.16.0)
* `uv` (for the vLLM venv), the HarnessAudit env (`.venv` or rc-kit venv)
* ~25 GB disk for vLLM + ~16 GB for Qwen3-8B weights

## 1. Install (once per machine/architecture)

```bash
scripts/local_qwen/01_check_environment.sh                 # what is missing (no downloads)
scripts/local_qwen/03_setup_qwen.sh                        # check only, exit 3 if missing
scripts/local_qwen/03_setup_qwen.sh --allow-download       # vLLM 0.33.0 + kernels, Qwen Code 0.27.4, Qwen3-8B
HA_MODEL=Qwen/Qwen3-14B scripts/local_qwen/03_setup_qwen.sh --allow-download --only model
```

Locations (all overridable): `HA_VLLM_VENV=~/venvs/HarnessAudit-vllm-<arch>`,
`HA_QWEN_CODE_PREFIX=~/.local/share/harnessaudit/qwen-code-<arch>`, weights in
`HF_HUB_CACHE` (default `~/.cache/huggingface/hub`). Weights are downloaded once
and shared by every server and agent.

## 2. Check costs and GPUs

```bash
STATIC=1 scripts/local_qwen/00_check_costs.sh     # before a server exists
scripts/local_qwen/02_gpu_status.sh               # free memory, load, permitted GPUs
python -m multi_agent.local_inference plan        # where a server would go (dry run)
```

## 3. Start a server

```bash
scripts/local_qwen/04_start_server_auto.sh                      # auto: one idle GPU
scripts/local_qwen/05_start_server_explicit.sh 0,2,3            # only GPUs 0,2,3 (3 replicas)
ALLOW_SHARED=1 SAFETY_MARGIN_GB=8 scripts/local_qwen/05_start_server_explicit.sh 1   # shared GPU
DRY_RUN=1 scripts/local_qwen/04_start_server_auto.sh            # plan + validated command only
```

or directly: `python -m multi_agent.local_inference serve --gpu auto` /
`--gpu-ids 0,2,3`. A healthy server for the same model is reused, never
duplicated. The server binds `127.0.0.1:8000`.

## 4. Verify

```bash
scripts/local_qwen/00_check_costs.sh          # must print LOCAL_ONLY_VERIFIED, exit 0
scripts/local_qwen/06_test_inference.sh       # chat, JSON output, reasoning parser, concurrency, timeout
scripts/local_qwen/07_test_tool_calls.sh      # structured tool calls, schema-validated, two turns
scripts/local_qwen/08_test_clawteam.sh        # real ClawTeam run + 11 integration checks
```

## 5. Experiments

```bash
# clean fin-t1 with local agents (judge skipped; JUDGE=1 uses the local judge)
scripts/local_qwen/09_run_clean_finance.sh
REPEAT=3 JUDGE=1 scripts/local_qwen/09_run_clean_finance.sh

# the same through the original CLI
python -m multi_agent run multi_agent/tasks/finance/financial_planning/fin-t1.yaml \
  --framework clawteam --harness qwen_code --skip-judge --agent-timeout 900

# SI-ATG: Qwen Code attacker vs local ClawTeam agents, 3 rounds
scripts/local_qwen/10_run_attacker_campaign.sh
python -m multi_agent.attacks campaign \
  --task multi_agent/tasks/finance/financial_planning/fin-t1.yaml \
  --backend qwen_code --framework clawteam --harness qwen_code \
  --rounds 3 --execute-target --max-target-runs 3 --skip-judge

# resume an interrupted campaign
RESUME=1 OUT_DIR=<campaign run dir> CAMPAIGN_ID=<id> ROUNDS=6 scripts/local_qwen/10_run_attacker_campaign.sh
```

## 6. Benchmarks, report, shutdown

```bash
scripts/local_qwen/11_benchmark_parallel.sh                 # tp1/tp2/tp4/dp2/dp4/separate servers
CONFIGS="tp1_auto dp2" CONCURRENCY=1,4 scripts/local_qwen/11_benchmark_parallel.sh
scripts/local_qwen/13_generate_report.sh                    # results/local_qwen/report_*/REPORT.md
scripts/local_qwen/12_stop_own_server.sh                    # stops only our server(s)
```

## 7. Configuration

`configs/local_qwen.yaml` (annotated) is the central file. Precedence: defaults <
file < cluster profile (`HA_CLUSTER_PROFILE`/`--profile`) < `HA_*` variables <
`HA_SET="a.b=1,c.d=2"` < `--set a.b=1`. Useful variables: `HA_INFERENCE_MODE`,
`HA_MODEL`, `HA_LOCAL_ENDPOINT`, `HA_GPU_IDS`, `HA_ALLOW_SHARED_GPU`,
`HA_MAX_MODEL_LEN`, `HA_TENSOR_PARALLEL_SIZE`, `HA_ROUTE_<ROLE>_MODEL`,
`HA_ROUTE_<ROLE>_ENDPOINT`.

Different models per role (e.g. a stronger target, smaller attacker): start a
second server on another port and point the role at it:

```yaml
routes:
  target_coordinator: {model: Qwen/Qwen3-14B, endpoint: http://127.0.0.1:8001/v1}
  target_specialists: {model: Qwen/Qwen3-14B, endpoint: http://127.0.0.1:8001/v1}
```

Attacker and target can share weights (same server) while keeping separate
prompts, Qwen Code homes, workspaces and memory — that is the default.

## 8. Reproducibility

Every report JSON carries `inference_provenance`: model id and revision, license,
quantization, backend and version, Qwen Code version, GPU/TP/replica layout,
context length, sampling, judge model and endpoint class, attacker backend/model,
harness config, task YAML SHA-256, git commit (+ dirty flag), and links to the
native Qwen Code artifacts. Campaign `config_snapshot.yaml` holds the same block.
Results are written under `results/local_qwen/...` and `.../qwen_code/<model>/`,
separate from hosted-model results. **Changing the judge or target model changes
the evaluation setting; do not pool these numbers with published HarnessAudit
results (GPT/Claude targets, GPT judge).**
