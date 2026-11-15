#!/usr/bin/env bash
# rc/submit.sh MODE [extra sbatch flags]   — run from anywhere, on SPORC or TIGRIS.
#   check     GPU + CUDA + imports sanity test (once after every env rebuild)
#   prep      PREP_CMD as a CPU job (downloads)
#   smoke     SMOKE_CMD on GPU (a few examples)
#   full      FULL_CMD on GPU (the real run)
#   "cmd"     any quoted command, e.g. rc/submit.sh "python eval.py --split test"
# Examples:   rc/submit.sh full --time=2-00:00:00 --mem=96G
set -euo pipefail
umask 0007
source "$(dirname "$0")/common.sh"
cd "$PROJECT_ROOT"

MODE="${1:-}"
[[ -n "$MODE" ]] || { sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 1; }
shift

rc_activate >/dev/null        # fail here, in seconds, instead of after queueing

case "$MODE" in
  check) NAME=check; TIME=$TIME_CHECK ;;
  prep)  NAME=prep;  TIME=$TIME_PREP;  [[ -n "$PREP_CMD"  ]] || rc_die "PREP_CMD is empty in rc/project.env" ;;
  smoke) NAME=smoke; TIME=$TIME_SMOKE; [[ -n "$SMOKE_CMD" ]] || rc_die "SMOKE_CMD is empty in rc/project.env" ;;
  full)  NAME=full;  TIME=$TIME_FULL;  [[ -n "$FULL_CMD"  ]] || rc_die "FULL_CMD is empty in rc/project.env" ;;
  *)     NAME=custom; TIME=$TIME_SMOKE ;;
esac

ARGS=(--parsable --account="$RC_ACCOUNT" --job-name="${REPO}-${NAME}"
      --nodes=1 --ntasks=1 --cpus-per-task="$CPUS" --mem="$MEM" --time="$TIME"
      --output="logs/%x_%j.out" --error="logs/%x_%j.err")
if [[ "$MODE" == prep || "$GPUS" == 0 ]]; then
  ARGS+=(--partition="$CPU_PARTITION")
else
  ARGS+=(--partition="$PARTITION" --gres="gpu:${GPU_TYPE}:${GPUS}")
fi

if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  rc_info "WARNING: tracked files are modified on RIT — this run won't match any commit."
fi

mkdir -p logs
JOBID=$(sbatch "${ARGS[@]}" "$@" rc/job.slurm "$MODE")
JOBID=${JOBID%%;*}
echo "$JOBID" > "logs/last_${CLUSTER}.jobid"
echo "$JOBID" > "logs/last_${NAME}_${CLUSTER}.jobid"

rc_info "submitted ${REPO}-${NAME} as job $JOBID on $CLUSTER (limit $TIME, commit $(git rev-parse --short HEAD))"
rc_info "wait:    rc/wait.sh      (waits, then shows status)"
rc_info "follow:  tail -f logs/${REPO}-${NAME}_${JOBID}.out     (Ctrl+C stops watching, not the job)"
