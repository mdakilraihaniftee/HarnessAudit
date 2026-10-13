#!/usr/bin/env bash
# Shared setup for SI-ATG experiment scripts. Run scripts from your HarnessAudit repo.
set -Eeuo pipefail

REPO_ROOT="${REPO_ROOT:-$PWD}"
cd "$REPO_ROOT"
TASK="${TASK:-multi_agent/tasks/finance/financial_planning/fin-t1.yaml}"
OUTPUT_ROOT="${OUTPUT_ROOT:-$REPO_ROOT/si_atg_experiments}"
mkdir -p "$OUTPUT_ROOT"

verify_repo() {
  if [[ ! -f "$TASK" ]]; then
    echo "ERROR: finance task not found at: $TASK" >&2
    echo "Run from your HarnessAudit repo, or set TASK=/path/to/fin-t1.yaml." >&2
    exit 2
  fi
  if ! python -c 'import multi_agent.attacks' >/dev/null 2>&1; then
    echo "ERROR: cannot import multi_agent.attacks; activate the correct Python environment." >&2
    exit 2
  fi
}

require_real_optin() {
  if [[ "${RUN_REAL:-NO}" != "YES" ]]; then
    echo "Real target execution is disabled by default." >&2
    echo "Run with RUN_REAL=YES to explicitly launch your local synthetic agents." >&2
    exit 2
  fi
}

# Attach a documented option only if this local version advertises it.
add_if_supported() {
  local cmd="$1" option="$2" value="$3"
  if python -m multi_agent.attacks "$cmd" --help 2>&1 | grep -Fq -- "$option"; then
    EXTRA_FLAGS+=("$option" "$value")
  else
    echo "NOTE: local '$cmd' does not advertise $option; skipping it." >&2
  fi
}

run_logged() {
  local label="$1"; shift
  local logfile="$OUTPUT_ROOT/${label}_$(date +%Y%m%d_%H%M%S).log"
  printf '\nRunning:'
  printf ' %q' "$@"
  printf '\nLog: %s\n\n' "$logfile"
  set +e
  "$@" 2>&1 | tee "$logfile"
  local rc=${PIPESTATUS[0]}
  set -e
  echo "Exit code: $rc | Log: $logfile"
  return "$rc"
}
