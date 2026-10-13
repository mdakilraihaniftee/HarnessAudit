#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"
verify_repo
require_real_optin
RUNS="${RUNS:-3}"
SKIP_JUDGE="${SKIP_JUDGE:-1}"
[[ "$RUNS" =~ ^[1-9][0-9]*$ ]] || { echo 'RUNS must be a positive integer' >&2; exit 2; }
cmd=(python -m multi_agent.attacks baseline --task "$TASK" \
  --runs "$RUNS" --execute-target --max-target-runs "$RUNS")
if [[ "$SKIP_JUDGE" == "1" ]]; then cmd+=(--skip-judge); fi
run_logged "baseline_${RUNS}runs" "${cmd[@]}"
echo 'CHECK: baseline violation counts/types, normalized action counts, task completion, and run failures.'
