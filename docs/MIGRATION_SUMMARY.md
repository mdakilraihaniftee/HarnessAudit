# Migration summary: hosted models → local Qwen (default)

## What changed for a user

| Before | Now (default `local_only`) |
|---|---|
| `multi_agent run` defaulted to `--harness claude --model sonnet --judge-model gpt-5.4` | defaults to `--harness qwen_code`, the locally served model, and the local judge route |
| judges called OpenAI Responses (or OpenRouter) | judges call the local vLLM route (chat completions, JSON mode, temperature 0) |
| SI-ATG default target: `codex` / `gpt-5.6-sol` | `qwen_code` / local model; attacker backends `qwen_code`, `local_openai` |
| a missing judge silently scored 0.0 | a missing local server fails the run before agents start |
| hosted paths always available | hosted paths refused unless `HA_INFERENCE_MODE=paid_opt_in` |

The legacy behaviour is fully preserved under `paid_opt_in`
(`multi_agent/attacks/configs/finance_paid.yaml` keeps the old SI-ATG settings).

## Files

### New

| Path | Purpose |
|---|---|
| `multi_agent/local_inference/config.py` | central config, precedence, cluster profiles, provenance of settings |
| `multi_agent/local_inference/endpoints.py` | endpoint/credential classification (LOCAL / APPROVED / COMMERCIAL / UNKNOWN) |
| `multi_agent/local_inference/router.py` | per-role model routes, cost guard, run/attacker policy, child-env scrubbing |
| `multi_agent/local_inference/cost_audit.py` | call-path inventory, `audit` / `verify-cost` |
| `multi_agent/local_inference/gpu_discovery.py` | NVML (ctypes) / nvidia-smi / mock discovery, permitted devices |
| `multi_agent/local_inference/gpu_memory.py`, `model_registry.py` | memory estimator, model facts (local `config.json` first) |
| `multi_agent/local_inference/gpu_allocator.py` | auto/explicit/scheduler placement, TP vs replicas, reservations |
| `multi_agent/local_inference/cluster.py` | Slurm context, host-list expansion, sbatch flags |
| `multi_agent/local_inference/server.py` | vLLM launch (flag validation, OOM retry), status, safe stop |
| `multi_agent/local_inference/health.py` | health, structured output, reasoning parser, tool calling, concurrency, timeout |
| `multi_agent/local_inference/performance.py` | request accounting, serving benchmark, GPU sampler, JSON/CSV export |
| `multi_agent/local_inference/provenance.py` | reproducibility block written into every report |
| `multi_agent/local_inference/cli.py`, `__main__.py` | `python -m multi_agent.local_inference …` |
| `multi_agent/frameworks/native/qwen_code/{runtime,ingestor}.py`, `assets/` | Qwen Code harness, stream-json ingestion, wrapper, timestamper, egress logger |
| `multi_agent/attacks/qwen_backend.py` | `QwenCodeAttackerBackend`, `LocalOpenAIAttackerBackend` |
| `multi_agent/attacks/configs/finance_paid.yaml` | the previous (hosted) SI-ATG config, unchanged |
| `configs/local_qwen.yaml`, `configs/cluster/{l40_shared,a100_single,a100_multinode,gh200}.yaml` | configuration |
| `scripts/local_qwen/00…13_*.sh`, `lib.sh`, `verify_clawteam_run.py`, `summarize_runs.py`, `report_benchmarks.py` | runbooks |
| `scripts/hpc/*.sbatch`, `common_hpc.sh`, `submit_campaign.sh` | Slurm launchers |
| `scripts/tests/test_{local_stack,gpu_selection,cost_guard,resume_campaign}.sh` | test drivers |
| `scripts/scenarios/scenario{1..7}_*.sh` | the seven requested deployment scenarios |
| `tests/test_local_inference.py`, `tests/test_qwen_code_ingestor.py`, `tests/fixtures/qwen_code/*`, `tests/fixtures/synthetic/syn-mailbox.yaml` | tests and fixtures (real traces) |
| `docs/*.md` (7 files) | documentation |

### Modified

| Path | Change |
|---|---|
| `multi_agent/cli.py` | `--harness qwen_code`; mode-aware defaults; cost-guard policy + endpoint preflight in `build_run_context`; `--inference-mode`, `--local-config`; `inference_provenance` and `wall_time_s` in reports |
| `multi_agent/openai_json.py` | local route path (chat completions, JSON mode); `LocalEndpointUnavailable`; refuses non-local judge models; legacy path unchanged under `paid_opt_in` |
| `multi_agent/{completion,operational}_judge.py`, `perturbation_eval.py` | pass their judge role to `openai_json` (one line each) |
| `multi_agent/frameworks/clawteam/{profiles,runtime}.py` | register the `qwen_code` profile/runtime |
| `multi_agent/frameworks/clawteam/adapter.py` | role-instruction hook; early end of the wait when every agent exited; hub-inbox preservation (default for `qwen_code` only) |
| `multi_agent/frameworks/clawteam/process_tree.py` | resolve `clawteam-mcp` from the running interpreter's env before `PATH` (a stale `.venv.pre-uv` on `PATH` was being used) |
| `multi_agent/attacks/{cli,config,agent}.py` | new backends, local defaults, cost guard before generation, `--max-parallel-trials` for independent baselines, provenance, per-round prompt + memory-id recording |
| `multi_agent/attacks/configs/finance.yaml` | local target/judge defaults |
| `single_agent/cli.py` | refuses (OpenRouter-only) under `local_only` |
| `multi_agent/run_ma.sh` | mode-aware defaults (`qwen_code` locally) |
| `rc/project.env` | comment: hosted SMOKE/FULL need `paid_opt_in` |
| `.gitignore` | track the new script directories and the wrapper asset |
| `README.md` | short local-Qwen section |

## Behavioural fixes that also touch pre-existing code

1. **Hub inbox consumed by the runner** (all ClawTeam harnesses): see
   `docs/QWEN_HARNESS_INTEGRATION.md`. Fixed by default only for `qwen_code`;
   `MASP_PRESERVE_HUB_INBOX=1` enables it for hosted harnesses. Decide before
   comparing new hosted runs with published ones.
2. **Idle wait to timeout** after all agents exited: now ends early
   (`MASP_WAIT_FULL_TIMEOUT=1` restores). Scheduling only.
3. **`clawteam-mcp` from a stale environment** on `PATH`: now from the active env.
4. **Silent 0.0 judge scores** when the judge endpoint is unreachable (local mode):
   now an explicit error.

## Comparability

Local-Qwen results use a different target model, attacker and (if enabled)
judge. Reports carry `inference_provenance`, artifacts live under
`…/qwen_code/<model>/` and `results/local_qwen/`, and `run_id`s contain
`qwen_code` and the model name, so they cannot be confused with hosted runs.
Do not pool them with published HarnessAudit numbers.
