#!/usr/bin/env bash
# Shared implementation; source from one of the eight numbered entry points.
set -euo pipefail
PHASE1_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PHASE1_ROOT"
PHASE1_PYTHON="${PYTHON_CMD:-$PHASE1_ROOT/.venv/bin/python}"
[[ -x "$PHASE1_PYTHON" ]] || { echo 'Set PYTHON_CMD to the installed HarnessAudit Python environment' >&2; exit 2; }
export PATH="$(dirname -- "$PHASE1_PYTHON"):$PATH"
export HA_INFERENCE_MODE=local_only
export HA_ALLOW_MODEL_DOWNLOAD=false
export MAX_PARALLEL_TRIALS="${MAX_PARALLEL_TRIALS:-1}"
[[ "$MAX_PARALLEL_TRIALS" == 1 ]] || { echo 'Phase 1 trials must run sequentially' >&2; exit 2; }
phase1_run() {
  local condition="$1" label="$2" repeats="$3" budget="$4"
  local -a conditions=("$condition") extra=()
  [[ "$condition" == pilot ]] && conditions=(B0 B1 B2)
  if [[ "${RUN_MOCK:-NO}" == YES ]]; then
    extra+=(--mock)
  else
    [[ "${RUN_REAL:-NO}" == YES ]] || { echo 'Real target disabled. Set RUN_REAL=YES and GPU_IDS explicitly; RUN_MOCK=YES runs a labeled integration fixture.' >&2; return 2; }
    [[ -n "${GPU_IDS:-}" ]] || { echo 'GPU_IDS is required, e.g. GPU_IDS=1,3' >&2; return 2; }
    extra+=(--run-real --gpu-ids "$GPU_IDS")
  fi
  [[ "${SKIP_JUDGE:-1}" == 1 ]] && extra+=(--skip-judge) || extra+=(--no-skip-judge)
  "$PHASE1_PYTHON" -m multi_agent.research_phase1 run --condition "${conditions[@]}" \
    --task "${TASK:-fin-t1}" --repeats "$repeats" --max-target-runs "$budget" \
    --seed "${SEED:-0}" --model "${MODEL_ID:-Qwen/Qwen3-8B}" \
    --model-endpoint "${MODEL_ENDPOINT:-http://127.0.0.1:8000/v1}" \
    --tensor-parallel-size "${TENSOR_PARALLEL_SIZE:-auto}" \
    --output-dir "${OUTPUT_DIR:-results/research_phase1/$label}" \
    --agent-timeout "${AGENT_TIMEOUT:-300}" --max-turns "${MAX_TURNS:-30}" "${extra[@]}"
}
