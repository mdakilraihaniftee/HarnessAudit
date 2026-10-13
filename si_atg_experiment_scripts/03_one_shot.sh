#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"
verify_repo
require_real_optin
ARM="${ARM:-B2}"
REPEATS="${REPEATS:-1}"
MAX_WALL_TIME="${MAX_WALL_TIME:-1200}"
SKIP_JUDGE="${SKIP_JUDGE:-1}"
[[ "$ARM" == B1 || "$ARM" == B2 || "$ARM" == B3 ]] || { echo 'ARM must be B1, B2, or B3' >&2; exit 2; }
[[ "$REPEATS" =~ ^[1-9][0-9]*$ ]] || { echo 'REPEATS must be positive' >&2; exit 2; }
BACKEND="mock"
if [[ "$ARM" == B3 ]]; then BACKEND="codex_cli"; fi

for ((i=1;i<=REPEATS;i++)); do
  name="${ARM}_repeat${i}_$(date +%Y%m%d_%H%M%S)"
  rundir="$OUTPUT_ROOT/$name"
  mkdir -p "$rundir"
  EXTRA_FLAGS=()
  add_if_supported campaign --output-dir "$rundir"
  add_if_supported campaign --memory-db "$rundir/attacker_memory.sqlite"
  cmd=(python -m multi_agent.attacks campaign \
    --task "$TASK" --arm "$ARM" --backend "$BACKEND" --rounds 1 \
    --execute-target --max-target-runs 1 --max-wall-time "$MAX_WALL_TIME")
  if [[ "$SKIP_JUDGE" == "1" ]]; then cmd+=(--skip-judge); fi
  cmd+=("${EXTRA_FLAGS[@]}")
  run_logged "$name" "${cmd[@]}"
done

echo 'CHECK: submitted requester note, whether the hub saw it, agent messages, violations, and final completion.'
echo 'A single success is only a candidate finding; independent repeats are needed.'
