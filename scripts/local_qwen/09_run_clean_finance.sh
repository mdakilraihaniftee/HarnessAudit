#!/usr/bin/env bash
# 09_run_clean_finance.sh — clean (no attacker) fin-t1 runs with local Qwen agents.
#
# Environment overrides:
#   TASK=multi_agent/tasks/finance/financial_planning/fin-t1.yaml
#   REPEAT=1            number of sequential runs
#   JUDGE=0             1 = score LLM checkpoints with the *local* judge route
#   AGENT_TIMEOUT=900   MAX_TURNS=30
#   SKIP_COST_CHECK=0   1 = skip the verify-cost preflight (not recommended)
# Usage:
#   scripts/local_qwen/09_run_clean_finance.sh
#   REPEAT=3 JUDGE=1 scripts/local_qwen/09_run_clean_finance.sh
# Output: results/local_qwen/clean_finance_<ts>/ (traces, results, native agent
# transcripts, gpu_samples.csv, summary.json/.csv)
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
ha_require_python
TASK="${TASK:-multi_agent/tasks/finance/financial_planning/fin-t1.yaml}"
REPEAT="${REPEAT:-1}"
OUT="${OUT_DIR:-$(ha_new_run_dir clean_finance)}"
mkdir -p "$OUT"
JOB_TMP="${HARNESSAUDIT_JOB_TMP:-${TMPDIR:-/tmp}/harnessaudit_${SLURM_JOB_ID:-local}_$$}"
export CLAWTEAM_DATA_DIR="${CLAWTEAM_DATA_DIR:-$JOB_TMP/clawteam}"
export MASP_WORKSPACES_ROOT="${MASP_WORKSPACES_ROOT:-$JOB_TMP/workspaces}"
export MASP_MCP_LOG_DIR="$OUT/mcp_logs" PYTHONUNBUFFERED=1
mkdir -p "$CLAWTEAM_DATA_DIR" "$MASP_WORKSPACES_ROOT"
if [[ "${SKIP_COST_CHECK:-0}" != 1 ]]; then
  ha_li verify-cost > "$OUT/verify_cost.txt" || { cat "$OUT/verify_cost.txt"; ha_die "cost check failed; refusing to run" 2; }
fi
SAMPLER=""
if command -v nvidia-smi >/dev/null; then
  nvidia-smi --query-gpu=timestamp,index,memory.used,utilization.gpu --format=csv,noheader -l 2 \
    > "$OUT/gpu_samples.csv" 2>/dev/null &
  SAMPLER=$!
fi
cleanup() { [[ -n "$SAMPLER" ]] && kill "$SAMPLER" 2>/dev/null || true; rm -rf "$JOB_TMP"; }
trap cleanup EXIT
fail=0
for i in $(seq 1 "$REPEAT"); do
  args=(-m multi_agent run "$TASK" --framework clawteam --harness qwen_code
        --agent-timeout "${AGENT_TIMEOUT:-900}" --max-turns "${MAX_TURNS:-30}"
        --output "$OUT/results" --trace-dir "$OUT/traces" --judge-workers "${JUDGE_WORKERS:-1}")
  [[ "${JUDGE:-0}" == 1 ]] || args+=(--skip-judge)
  ha_info "run $i/$REPEAT -> $OUT/run_$i.log"
  start=$(date +%s.%N)
  set +e
  "$HA_PYTHON" "${args[@]}" > "$OUT/run_$i.log" 2>&1
  rc=$?
  set -e
  end=$(date +%s.%N)
  run_id=$(sed -n 's/^Report saved to .*_\(ct-[^/]*\)\.json$/\1/p' "$OUT/run_$i.log" | tail -1)
  printf '%s\t%s\t%s\n' "${run_id:-run_$i}" "$(echo "$end - $start" | bc)" "$rc" >> "$OUT/times.tsv"
  tail -n 18 "$OUT/run_$i.log"
  (( rc == 0 )) || fail=1
done
"$HA_PYTHON" "$HA_ROOT/scripts/local_qwen/summarize_runs.py" "$OUT/results" "$OUT/summary" \
  --gpu-samples "$OUT/gpu_samples.csv" --times "$OUT/times.tsv" | tee "$OUT/summary.txt"
ha_info "artifacts: $OUT"
exit $fail
