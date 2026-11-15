#!/usr/bin/env bash
# rc/wait.sh [JOBID] — wait until a job leaves the queue, then show rc/status.sh.
# Defaults to the last job submitted on this cluster. Typical use:
#     rc/submit.sh smoke && rc/wait.sh
# Ctrl+C stops waiting only; the job keeps running.
set -euo pipefail
source "$(dirname "$0")/common.sh"
cd "$PROJECT_ROOT"

JOBID="${1:-$(cat "logs/last_${CLUSTER}.jobid" 2>/dev/null || true)}"
[[ -n "$JOBID" ]] || rc_die "no JOBID given and no logs/last_${CLUSTER}.jobid yet"

rc_info "waiting for job $JOBID (Ctrl+C stops waiting, not the job)"
while state=$(squeue -h -j "$JOBID" -o %T 2>/dev/null) && [[ -n "$state" ]]; do
  printf '\rrc: %-10s %s ' "$state" "$(date +%H:%M:%S)" >&2
  sleep "${RC_POLL:-20}"
done
echo >&2
exec bash "$PROJECT_ROOT/rc/status.sh" "$JOBID"
