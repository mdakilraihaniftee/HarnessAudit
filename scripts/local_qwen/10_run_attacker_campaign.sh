#!/usr/bin/env bash
# 10_run_attacker_campaign.sh — SI-ATG campaign with a local Qwen attacker
# (Qwen Code harness) against local Qwen ClawTeam agents.
#
# Reuses the running local server (never loads a duplicate model copy). With
# START_SERVER=1 it starts one via 04_start_server_auto.sh only if none is healthy.
#
# Environment overrides:
#   ROUNDS=3  ARM=B6  BACKEND=qwen_code (qwen_code|local_openai|mock)
#   EXECUTE_TARGET=1 (0 = dry-run: attacker generates, target not executed)
#   BASELINE_RUNS=0  MAX_PARALLEL_TRIALS=1 (baselines only; rounds stay sequential)
#   MAX_TARGET_RUNS=ROUNDS+BASELINE_RUNS   AGENT_TIMEOUT=900  MAX_WALL_TIME=14400
#   JUDGE=0          CAMPAIGN_ID=<new>     MEMORY_DB=<run dir>/attacker_memory.sqlite
#   OUT_DIR=<new run dir>  (pass the same OUT_DIR + CAMPAIGN_ID with RESUME=1 to resume)
# Usage:
#   scripts/local_qwen/10_run_attacker_campaign.sh
#   ROUNDS=10 BASELINE_RUNS=2 MAX_PARALLEL_TRIALS=2 scripts/local_qwen/10_run_attacker_campaign.sh
#   RESUME=1 OUT_DIR=results/local_qwen/campaign_X CAMPAIGN_ID=abc scripts/local_qwen/10_run_attacker_campaign.sh
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
ha_require_python
TASK="${TASK:-multi_agent/tasks/finance/financial_planning/fin-t1.yaml}"
ROUNDS="${ROUNDS:-3}"; ARM="${ARM:-B6}"; BACKEND="${BACKEND:-qwen_code}"
BASELINE_RUNS="${BASELINE_RUNS:-0}"
MAX_TARGET_RUNS="${MAX_TARGET_RUNS:-$((ROUNDS + BASELINE_RUNS))}"
OUT="${OUT_DIR:-$(ha_new_run_dir campaign)}"
mkdir -p "$OUT"
MEMORY_DB="${MEMORY_DB:-$OUT/attacker_memory.sqlite}"
JOB_TMP="${HARNESSAUDIT_JOB_TMP:-${TMPDIR:-/tmp}/harnessaudit_${SLURM_JOB_ID:-local}_$$}"
export CLAWTEAM_DATA_DIR="${CLAWTEAM_DATA_DIR:-$JOB_TMP/clawteam}"
export MASP_WORKSPACES_ROOT="${MASP_WORKSPACES_ROOT:-$JOB_TMP/workspaces}"
export MASP_MCP_LOG_DIR="$OUT/mcp_logs" PYTHONUNBUFFERED=1
mkdir -p "$CLAWTEAM_DATA_DIR" "$MASP_WORKSPACES_ROOT"
trap 'rm -rf "$JOB_TMP"' EXIT

servers_before=$(ha_li status --json | "$HA_PYTHON" -c 'import json,sys; print(sum(s["healthy"] for s in json.load(sys.stdin)))')
if ! ha_li verify-cost --attacker-backend "$BACKEND" > "$OUT/verify_cost.txt"; then
  if [[ "${START_SERVER:-0}" == 1 ]]; then
    "$HA_ROOT/scripts/local_qwen/04_start_server_auto.sh"
    ha_li verify-cost --attacker-backend "$BACKEND" > "$OUT/verify_cost.txt" \
      || { cat "$OUT/verify_cost.txt"; ha_die "cost check failed after server start" 2; }
  else
    cat "$OUT/verify_cost.txt"
    ha_die "cost check failed (is the local server running? START_SERVER=1 starts one)" 2
  fi
fi
grep -E "verdict|verify-cost" "$OUT/verify_cost.txt"

cmd=(-m multi_agent.attacks)
if [[ "${RESUME:-0}" == 1 ]]; then
  [[ -n "${CAMPAIGN_ID:-}" ]] || ha_die "RESUME=1 needs CAMPAIGN_ID" 2
  cmd+=(resume --campaign-id "$CAMPAIGN_ID")
else
  cmd+=(campaign)
  [[ -n "${CAMPAIGN_ID:-}" ]] && cmd+=(--campaign-id "$CAMPAIGN_ID")
  cmd+=(--baseline-runs "$BASELINE_RUNS")
fi
cmd+=(--task "$TASK" --arm "$ARM" --backend "$BACKEND" --framework clawteam
      --target-harness qwen_code --rounds "$ROUNDS" --max-target-runs "$MAX_TARGET_RUNS"
      --max-wall-time "${MAX_WALL_TIME:-14400}" --output-dir "$OUT" --memory-db "$MEMORY_DB"
      --max-parallel-trials "${MAX_PARALLEL_TRIALS:-1}")
if [[ "${EXECUTE_TARGET:-1}" == 1 ]]; then cmd+=(--execute-target); else cmd+=(--dry-run); fi
[[ "${JUDGE:-0}" == 1 ]] || cmd+=(--skip-judge)
# Longer agent timeout for local agents (campaign config copy with override).
CFG="$OUT/attacker_config.yaml"
"$HA_PYTHON" - "$CFG" "${AGENT_TIMEOUT:-900}" "$ROUNDS" <<'PY'
import sys, yaml
from pathlib import Path
base = yaml.safe_load(Path("multi_agent/attacks/configs/finance.yaml").read_text())
base["target_agent_timeout"] = int(sys.argv[2])
base["max_rounds"] = max(int(base.get("max_rounds", 10)), int(sys.argv[3]))
Path(sys.argv[1]).write_text(yaml.safe_dump(base, sort_keys=False))
PY
cmd+=(--config "$CFG")
start=$(date +%s)
ha_run_logged "$OUT/campaign.log" "$HA_PYTHON" "${cmd[@]}"
rc=$?
end=$(date +%s)
servers_after=$(ha_li status --json | "$HA_PYTHON" -c 'import json,sys; print(sum(s["healthy"] for s in json.load(sys.stdin)))')
echo "campaign wall time: $((end - start)) s; healthy servers before=$servers_before after=$servers_after" | tee -a "$OUT/campaign.log"
camp_dir=$(ls -d "$OUT"/campaign_* 2>/dev/null | tail -1)
[[ -n "$camp_dir" ]] && cat "$camp_dir/report.md" 2>/dev/null
ha_info "campaign artifacts: ${camp_dir:-$OUT}; memory: $MEMORY_DB"
exit $rc
