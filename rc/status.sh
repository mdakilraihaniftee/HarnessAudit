#!/usr/bin/env bash
# rc/status.sh [JOBID] — queue state, accounting, log tails and result counts.
# Defaults to the last job you submitted on this cluster. The output is meant to
# be pasted straight to the PI coding agent when something fails.
set -euo pipefail
source "$(dirname "$0")/common.sh"
cd "$PROJECT_ROOT"

JOBID="${1:-$(cat "logs/last_${CLUSTER}.jobid" 2>/dev/null || true)}"
[[ -n "$JOBID" ]] || rc_die "no JOBID given and no logs/last_${CLUSTER}.jobid yet"

echo "### $REPO | cluster=$CLUSTER | commit=$(git rev-parse --short HEAD) | job=$JOBID"
echo "--- your queue on $CLUSTER ---"
squeue --me -o "%.10i %.30j %.9T %.10M %.11l %R" || true
echo "--- accounting ---"
sacct -j "$JOBID" --format=JobID,JobName%30,State,ExitCode,Elapsed,Timelimit,ReqMem,MaxRSS,NodeList || true

shopt -s nullglob
for f in logs/*_"$JOBID".out logs/*_"$JOBID".err; do
  echo "--- tail $f ---"
  tail -n "${TAIL:-30}" "$f"
done

echo "--- results ($RESULT_GLOB) ---"
# shellcheck disable=SC2086   # RESULT_GLOB is meant to expand
for f in $RESULT_GLOB; do
  [[ -f "$f" ]] || continue
  case "$f" in
    *.jsonl|*.csv|*.tsv|*.txt) printf '%9d lines  %s\n' "$(wc -l < "$f")" "$f" ;;
    *) ls -lh "$f" ;;
  esac
done
