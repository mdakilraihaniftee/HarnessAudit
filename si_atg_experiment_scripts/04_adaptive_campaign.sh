#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"
verify_repo
require_real_optin
ARM="${ARM:-B5}"
ROUNDS="${ROUNDS:-3}"
MAX_WALL_TIME="${MAX_WALL_TIME:-3600}"
SKIP_JUDGE="${SKIP_JUDGE:-1}"
[[ "$ARM" == B4 || "$ARM" == B5 || "$ARM" == B6 ]] || { echo 'ARM must be B4, B5, or B6' >&2; exit 2; }
[[ "$ROUNDS" =~ ^[1-9][0-9]*$ ]] || { echo 'ROUNDS must be positive' >&2; exit 2; }
name="${ARM}_${ROUNDS}rounds_$(date +%Y%m%d_%H%M%S)"
rundir="$OUTPUT_ROOT/$name"
mkdir -p "$rundir"
EXTRA_FLAGS=()
add_if_supported campaign --output-dir "$rundir"
add_if_supported campaign --memory-db "$rundir/attacker_memory.sqlite"
cmd=(python -m multi_agent.attacks campaign \
  --task "$TASK" --arm "$ARM" --backend codex_cli --rounds "$ROUNDS" \
  --execute-target --max-target-runs "$ROUNDS" --max-wall-time "$MAX_WALL_TIME")
if [[ "$SKIP_JUDGE" == "1" ]]; then cmd+=(--skip-judge); fi
cmd+=("${EXTRA_FLAGS[@]}")
run_logged "$name" "${cmd[@]}"

echo "Campaign directory: $rundir"
echo 'CHECK: each round candidate, memory retrieval, selected strategy, violations, reward, harness version.'
if [[ "$ARM" == B6 && "$ROUNDS" -lt 10 ]]; then
  echo 'NOTE: <10 rounds may be insufficient for the configured harness holdout/promotion rule.'
fi
