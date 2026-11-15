#!/usr/bin/env bash
# Run the full multi-agent HarnessAudit task suite.

set -euo pipefail

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<'EOF'
Usage: [VAR=...] bash multi_agent/run_ma.sh

Runs all multi_agent/tasks/*.yaml files unless TASK_FILE or TASK_LIST is set.

Common variables:
  HARNESS=openclaw|claude|codex|oai|adk   default: openclaw
  FRAMEWORK=clawteam|oai|adk              default: clawteam, or HARNESS for oai/adk
  MODEL=gpt-5.4                           model passed to the harness
  TASK_WORKERS=1                           concurrent task runs
  JUDGE_MODEL=gpt-5.4                      judge model
  JUDGE_WORKERS=4                          concurrent judge calls per task
  SKIP_JUDGE=1                             skip LLM judges
  SKIP_EXISTING=1                          skip tasks with an error-free result JSON (resume)
  PYTHON_CMD=python                        interpreter (default: python on PATH)
  HARNESSAUDIT_JOB_TMP=/path               per-job scratch (default: ${TMPDIR:-/tmp}/harnessaudit_${SLURM_JOB_ID:-local})
  EXTRA_ARGS="--allow-stale-perturbation"
EOF
  exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

HARNESS="${HARNESS:-${MASP_HARNESS:-openclaw}}"
MODEL="${MODEL:-${MASP_MODEL:-gpt-5.4}}"
if [[ -n "${FRAMEWORK:-}" ]]; then
  FRAMEWORK="$FRAMEWORK"
elif [[ "$HARNESS" == "oai" || "$HARNESS" == "adk" ]]; then
  FRAMEWORK="$HARNESS"
else
  FRAMEWORK="clawteam"
fi

TASK_WORKERS="${TASK_WORKERS:-${WORKERS:-1}}"
JUDGE_WORKERS="${JUDGE_WORKERS:-4}"
JUDGE_MODEL="${JUDGE_MODEL:-gpt-5.4}"
SKIP_JUDGE="${SKIP_JUDGE:-0}"
AGENT_TIMEOUT="${AGENT_TIMEOUT:-300}"
MAX_TURNS="${MAX_TURNS:-30}"
OUTPUT_DIR="${OUTPUT_DIR:-multi_agent/results}"
TRACE_DIR="${TRACE_DIR:-multi_agent/traces}"
TASK_ROOT="${TASK_ROOT:-multi_agent/tasks}"
TASK_LIST="${TASK_LIST:-}"
TASK_FILE="${TASK_FILE:-}"
SKIP_EXISTING="${SKIP_EXISTING:-0}"
EXTRA_ARGS="${EXTRA_ARGS:-}"
PYTHON_CMD_STR="${PYTHON_CMD:-python}"
read -r -a PYTHON_CMD <<< "$PYTHON_CMD_STR"
read -r -a EXTRA_ARGV <<< "$EXTRA_ARGS"

MODEL_DIR="${MODEL//\//__}"
LOG_DIR="${LOG_DIR:-$OUTPUT_DIR/_run_logs/$HARNESS/$MODEL_DIR}"
mkdir -p "$LOG_DIR" "$OUTPUT_DIR" "$TRACE_DIR"

# Per-job scratch: several Slurm jobs may share a node, so nothing goes to a fixed /tmp path.
JOB_TMP="${HARNESSAUDIT_JOB_TMP:-${TMPDIR:-/tmp}/harnessaudit_${SLURM_JOB_ID:-local}}"
mkdir -p "$JOB_TMP"
export MASP_WORKSPACES_ROOT="${MASP_WORKSPACES_ROOT:-$JOB_TMP/workspaces}"
# ClawTeam team/task/workspace state (default ~/.clawteam is shared by every job on both clusters).
export CLAWTEAM_DATA_DIR="${CLAWTEAM_DATA_DIR:-$JOB_TMP/clawteam}"
OPENCLAW_PLUGINS_SNAPSHOT="${OPENCLAW_PLUGINS_SNAPSHOT:-$JOB_TMP/openclaw_plugins_enabled.json}"
# One line per finished task ("done"/"error"/"fail"); rc/status.sh counts these lines.
PROGRESS_FILE="$OUTPUT_DIR/progress_${HARNESS}_${MODEL_DIR}.txt"
{
  echo "date=$(date -Is) host=$(hostname) slurm_job=${SLURM_JOB_ID:-none}"
  echo "git_commit=$(git rev-parse HEAD 2>/dev/null || echo unknown)"
  for v in FRAMEWORK HARNESS MODEL TASK_WORKERS JUDGE_MODEL JUDGE_WORKERS SKIP_JUDGE AGENT_TIMEOUT \
           MAX_TURNS OUTPUT_DIR TRACE_DIR TASK_ROOT TASK_LIST TASK_FILE SKIP_EXISTING EXTRA_ARGS \
           PYTHON_CMD_STR JOB_TMP MASP_WORKSPACES_ROOT CLAWTEAM_DATA_DIR; do
    echo "$v=${!v}"
  done
} > "$LOG_DIR/run_config_$(date +%Y%m%dT%H%M%S)_${SLURM_JOB_ID:-local}.txt"

if [[ "$HARNESS" == "openclaw" ]]; then
  echo "[run_ma] capturing OpenClaw enabled plugin snapshot"
  openclaw plugins list --enabled --json > "$OPENCLAW_PLUGINS_SNAPSHOT"
  cp "$OPENCLAW_PLUGINS_SNAPSHOT" "$LOG_DIR/openclaw_plugins_enabled.json"
fi

TASKS_TMP="$(mktemp)"
cleanup() {
  rm -f "$TASKS_TMP"
  # Under Slurm the scratch dir belongs to this job only; leave it when debugging.
  if [[ -n "${SLURM_JOB_ID:-}" && -z "${HARNESSAUDIT_JOB_TMP:-}" && "${MASP_KEEP_WORKSPACES:-0}" != "1" ]]; then
    rm -rf "$JOB_TMP"
  fi
}
trap cleanup EXIT

if [[ -n "$TASK_FILE" ]]; then
  printf '%s\n' "$TASK_FILE" > "$TASKS_TMP"
elif [[ -n "$TASK_LIST" ]]; then
  cp "$TASK_LIST" "$TASKS_TMP"
else
  find "$TASK_ROOT" -name '*.yaml' | sort > "$TASKS_TMP"
fi

TOTAL_TASKS="$(wc -l < "$TASKS_TMP" | tr -d ' ')"
echo "[run_ma] framework=$FRAMEWORK harness=$HARNESS model=$MODEL tasks=$TOTAL_TASKS task_workers=$TASK_WORKERS judge_model=$JUDGE_MODEL judge_workers=$JUDGE_WORKERS"

# result_ok FILE... -> exit 0 if any result JSON exists whose "error" field is empty.
# Runs that hit e.g. API quota/billing errors still write a result JSON; they must not
# count as finished, so SKIP_EXISTING=1 retries them on the next submission.
result_ok() {
  "${PYTHON_CMD[@]}" -c '
import json, sys
for path in sys.argv[1:]:
    try:
        with open(path, encoding="utf-8") as fh:
            if not json.load(fh).get("error"):
                sys.exit(0)
    except (OSError, ValueError):
        pass
sys.exit(1)
' "$@"
}

run_one() {
  local task="$1"
  local task_id
  task_id="$(basename "$task" .yaml)"
  local log_file="$LOG_DIR/${task_id}.log"
  local result_scope="$OUTPUT_DIR/$HARNESS/$MODEL_DIR"

  if [[ "$SKIP_EXISTING" == "1" ]] && compgen -G "$result_scope/${task_id}_*.json" > /dev/null \
     && result_ok "$result_scope/${task_id}"_*.json; then
    echo "[run_ma] skip existing $task_id"
    return 0
  fi

  local args=(
    -m multi_agent run "$task"
    --framework "$FRAMEWORK"
    --model "$MODEL"
    --output "$OUTPUT_DIR"
    --trace-dir "$TRACE_DIR"
    --max-turns "$MAX_TURNS"
    --agent-timeout "$AGENT_TIMEOUT"
    --judge-model "$JUDGE_MODEL"
    --judge-workers "$JUDGE_WORKERS"
  )
  if [[ "$FRAMEWORK" == "clawteam" ]]; then
    args+=(--harness "$HARNESS")
  fi
  if [[ "$SKIP_JUDGE" == "1" || "$SKIP_JUDGE" == "true" || "$SKIP_JUDGE" == "yes" ]]; then
    args+=(--skip-judge)
  fi
  if (( ${#EXTRA_ARGV[@]} > 0 )); then
    args+=("${EXTRA_ARGV[@]}")
  fi

  echo "[run_ma] start $task_id -> $log_file"
  if "${PYTHON_CMD[@]}" "${args[@]}" > "$log_file" 2>&1; then
    local report
    report="$(sed -n 's/^Report saved to //p' "$log_file" | tail -n 1)"
    if [[ -n "$report" ]] && ! result_ok "$report"; then
      echo "[run_ma] error $task_id: run recorded an error in $report (see $log_file)" >&2
      echo "$(date -Is) error $task_id" >> "$PROGRESS_FILE"
      return 1
    fi
    echo "[run_ma] done  $task_id"
    echo "$(date -Is) done $task_id" >> "$PROGRESS_FILE"
  else
    local status=$?
    echo "[run_ma] fail  $task_id status=$status log=$log_file" >&2
    echo "$(date -Is) fail $task_id status=$status" >> "$PROGRESS_FILE"
    return "$status"
  fi
}

fail=0
active=0
while IFS= read -r task; do
  [[ -z "$task" ]] && continue
  run_one "$task" &
  active=$((active + 1))
  if (( active >= TASK_WORKERS )); then
    if ! wait -n; then
      fail=1
    fi
    active=$((active - 1))
  fi
done < "$TASKS_TMP"

while (( active > 0 )); do
  if ! wait -n; then
    fail=1
  fi
  active=$((active - 1))
done

exit "$fail"
