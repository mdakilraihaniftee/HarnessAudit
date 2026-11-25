#!/usr/bin/env bash
# Manual-only R3: separate smoke, gated 45-trial diagnostic, or CPU packaging.
set -euo pipefail
TASK_REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$TASK_REPO_ROOT"
TASK_MODE="${1:-}"
TASK_RELEASE_DIR=results/research_phase1r/target_v1r3
TASK_SMOKE_DIR=results/research_phase1r/diagnostic_smoke/v4
TASK_DIAGNOSTIC_DIR=results/research_phase1r/diagnostic_calibration/r3_15_per_condition_v1
export HA_INFERENCE_MODE=local_only HA_ALLOW_MODEL_DOWNLOAD=false
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
case "$TASK_MODE" in
  export)
    export RUN_REAL=NO
    exec "$TASK_REPO_ROOT/.venv/bin/python" scripts/research_phase1/prepare_chatgpt_r3_review.py
    ;;
  package)
    export RUN_REAL=NO
    exec bash scripts/research_phase1/phase1r.sh package --results-dir results/research_phase1r
    ;;
  smoke)
    TASK_OUTPUT_DIR="$TASK_SMOKE_DIR"
    TASK_REPEATS=1
    TASK_REQUEST_BUDGET=1200
    ;;
  diagnostic)
    TASK_OUTPUT_DIR="$TASK_DIAGNOSTIC_DIR"
    TASK_REPEATS=15
    TASK_REQUEST_BUDGET=18000
    ;;
  *)
    echo "Usage: bash scripts/research_phase1/phase1r3_manual.sh {smoke|diagnostic|export|package}" >&2
    exit 2
    ;;
esac
if [[ -e "$TASK_OUTPUT_DIR" ]]; then
  echo "Refusing to replace $TASK_OUTPUT_DIR; preserve existing attempts." >&2
  exit 2
fi
if [[ ! -f "$TASK_RELEASE_DIR/TARGET_V1R3.json" ]]; then
  echo "Missing frozen TARGET_V1R3 release; CPU preparation is required." >&2
  exit 2
fi
if [[ "$TASK_MODE" == diagnostic ]]; then
  # Check before reserving a directory or invoking any GPU/resource operation.
  "$TASK_REPO_ROOT/.venv/bin/python" - "$TASK_SMOKE_DIR" "$TASK_RELEASE_DIR" <<'PY'
import json
import sys
from pathlib import Path

manifest = json.loads((Path(sys.argv[2]) / 'TARGET_V1R3.json').read_text())
path = Path(sys.argv[1]) / 'smoke_acceptance.json'
if not path.is_file():
    raise SystemExit(f'Smoke prerequisite missing: {path}')
smoke = json.loads(path.read_text())
if smoke.get('passed') is not True:
    raise SystemExit(f'Smoke failed: {path}; do not advance to diagnostics')
if smoke.get('target_identity') != manifest['target_identity']:
    raise SystemExit(f'Smoke target identity differs: {path}')
PY
fi
export GPU_IDS=0,1,2,3 RUN_REAL=YES
mkdir -p "${TASK_OUTPUT_DIR%/*}"
mkdir "$TASK_OUTPUT_DIR"
TASK_STATUS=0
bash scripts/research_phase1/phase1r.sh "$TASK_MODE" \
  --target-version TARGET_V1R3 \
  --target-manifest "$TASK_RELEASE_DIR/TARGET_V1R3.json" \
  --results-dir "$TASK_OUTPUT_DIR" --smoke-results-dir "$TASK_SMOKE_DIR" \
  --gpu-ids 0,1,2,3 --server-port 8000 --proxy-port 18081 \
  --repeats "$TASK_REPEATS" --master-seed 0 --parallel 1 \
  --model-request-budget "$TASK_REQUEST_BUDGET" --per-trial-requests 400 \
  --agent-timeout 300 --max-turns 30 2>&1 | tee "$TASK_OUTPUT_DIR/controller.log" || {
    TASK_PIPE_STATUS=("${PIPESTATUS[@]}")
    TASK_STATUS="${TASK_PIPE_STATUS[0]}"
    if [[ "$TASK_STATUS" -eq 0 ]]; then TASK_STATUS=2; fi
  }
"$TASK_REPO_ROOT/.venv/bin/python" - "$TASK_OUTPUT_DIR" "$TASK_STATUS" "$TASK_MODE" <<'PY'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
path = root / 'campaign.json'
campaign = json.loads(path.read_text()) if path.exists() else {}
receipt = dict(
    mode=sys.argv[3], exit_code=int(sys.argv[2]), retries=0,
    campaign_started=bool(campaign), controller_log='controller.log',
    scheduled_trials=len(campaign.get('schedule', [])),
    reserved_attempts=len(campaign.get('attempts', [])),
    cleanup=campaign.get('cleanup') if campaign else {'status': 'NO_CAMPAIGN_STARTED'},
    replace_invalid_trials=False,
)
(root / 'manual_launch_receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
print(json.dumps(receipt, indent=2))
PY
# Package failures too. This never advances to another experiment or retries.
bash scripts/research_phase1/phase1r.sh package --results-dir results/research_phase1r || {
  echo "Packaging failed; raw evidence remains in $TASK_OUTPUT_DIR." >&2
  exit 2
}
if [[ "$TASK_STATUS" -ne 0 ]]; then
  echo "Run failed or was blocked; inspect $TASK_OUTPUT_DIR. No retry started." >&2
fi
exit "$TASK_STATUS"
