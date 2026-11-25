#!/usr/bin/env bash
# TARGET_V1R4 manual launcher. Only `smoke` and `gpu-preflight` touch GPUs/models,
# and only when invoked by a person. Nothing here retries, advances to a larger
# study, or changes model, sampling, turn, request or timeout ceilings.
set -euo pipefail
TASK_REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$TASK_REPO_ROOT"
TASK_MODE="${1:-}"
TASK_PY="$TASK_REPO_ROOT/.venv/bin/python"
TASK_RELEASE_DIR=results/research_phase1r/target_v1r4
TASK_SMOKE_DIR=results/research_phase1r/diagnostic_smoke/v5
TASK_STUDY_DIR=results/research_phase1r/diagnostic_calibration/r4_5_per_condition_v1
TASK_V4_TRIAL=results/research_phase1r/diagnostic_smoke/v4/trials/B0-0
TASK_GPU_IDS="${GPU_IDS:-0,1,2,3}"
export HA_INFERENCE_MODE=local_only HA_ALLOW_MODEL_DOWNLOAD=false
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
unset OPENROUTER_API_KEY OPENAI_API_KEY ANTHROPIC_API_KEY 2>/dev/null || true

if [[ "$TASK_GPU_IDS" != "0,1,2,3" ]]; then
  # The frozen identity binds the four-replica TP=1 configuration on GPUs 0-3.
  echo "TARGET_V1R4 is frozen for GPU_IDS=0,1,2,3 (four TP=1 replicas); a different" >&2
  echo "GPU set changes the inference configuration and needs a new PI-approved release." >&2
  exit 2
fi

run_cli() { "$TASK_PY" -m multi_agent.research_phase1r "$@"; }

case "$TASK_MODE" in
  freeze)
    # CPU only: freeze TARGET_V1R4 once, in the same environment the smoke uses.
    export RUN_REAL=NO
    exec "$TASK_PY" -m multi_agent.research_phase1r freeze-r4
    ;;
  preflight)
    export RUN_REAL=NO
    run_cli verify-preservation
    exec "$TASK_PY" -m multi_agent.research_phase1r preflight-r4
    ;;
  test-tool-contract)
    export RUN_REAL=NO
    run_cli test-tool-contract --results-dir "$TASK_RELEASE_DIR/schema_evidence"
    exec "$TASK_PY" -m pytest -q tests/test_research_phase1r4.py
    ;;
  audit-v4)
    export RUN_REAL=NO
    exec "$TASK_PY" -m multi_agent.research_phase1r audit-trial \
      --trial-dir "$TASK_V4_TRIAL" --results-dir "$TASK_RELEASE_DIR/v4_audit"
    ;;
  gpu-preflight)
    # Read-only GPU inspection; does not start a model server.
    export RUN_REAL=NO
    mkdir -p "$TASK_RELEASE_DIR/gpu_preflight"
    exec "$TASK_PY" -m multi_agent.research_phase1r gpu-preflight \
      --gpu-ids "$TASK_GPU_IDS" --server-port 8000 \
      --results-dir "$TASK_RELEASE_DIR/gpu_preflight"
    ;;
  analyze)
    # CPU only: re-derive reports for whichever V1R4 campaigns exist.
    export RUN_REAL=NO
    TASK_FOUND=0
    for TASK_DIR in "$TASK_SMOKE_DIR" "$TASK_STUDY_DIR"; do
      [[ -f "$TASK_DIR/campaign.json" ]] || continue
      TASK_FOUND=1
      run_cli analyze --results-dir "$TASK_DIR"
      for TASK_TRIAL in "$TASK_DIR"/trials/*/; do
        [[ -d "$TASK_TRIAL" ]] || continue
        TASK_ID="$(basename "$TASK_TRIAL")"
        run_cli audit-trial --trial-dir "$TASK_TRIAL" \
          --results-dir "$TASK_DIR/derived/$TASK_ID" || true
      done
      run_cli study-summary --results-dir "$TASK_DIR"
    done
    [[ "$TASK_FOUND" -eq 1 ]] || { echo "No V1R4 campaign to analyze." >&2; exit 2; }
    exit 0
    ;;
  package|export)
    export RUN_REAL=NO
    exec "$TASK_PY" scripts/research_phase1/prepare_r4_review_bundle.py
    ;;
  smoke)
    # One trial per benign condition; stops at the first trial without valid measurement.
    TASK_OUT="$TASK_SMOKE_DIR"; TASK_REPEATS=1; TASK_BUDGET=1200
    ;;
  benign-study)
    # User-approved: 5 trials each for B0, B1n, B1p (15 total, randomized blocks).
    # Every trial runs and is kept; no retry, no replacement, no early stop.
    TASK_OUT="$TASK_STUDY_DIR"; TASK_REPEATS=5; TASK_BUDGET=6000
    ;;
  *)
    echo "Usage: bash scripts/research_phase1/phase1r4_manual.sh" \
      "{freeze|preflight|test-tool-contract|audit-v4|gpu-preflight|smoke|benign-study|analyze|package}" >&2
    exit 2
    ;;
esac

# ---- real run (smoke or benign-study) with TARGET_V1R4 -------------------------------
if [[ -e "$TASK_OUT" ]]; then
  echo "Refusing to replace $TASK_OUT; preserve existing attempts." >&2
  exit 2
fi
if [[ ! -f "$TASK_RELEASE_DIR/TARGET_V1R4.json" ]]; then
  echo "Missing frozen TARGET_V1R4 release; run the CPU freeze step first." >&2
  exit 2
fi
export GPU_IDS="$TASK_GPU_IDS" RUN_REAL=YES
mkdir -p "${TASK_OUT%/*}"
mkdir "$TASK_OUT"
TASK_STATUS=0
bash scripts/research_phase1/phase1r.sh "$TASK_MODE" \
  --target-version TARGET_V1R4 \
  --target-manifest "$TASK_RELEASE_DIR/TARGET_V1R4.json" \
  --results-dir "$TASK_OUT" --smoke-results-dir "$TASK_SMOKE_DIR" \
  --gpu-ids "$TASK_GPU_IDS" --server-port 8000 --proxy-port 18081 \
  --repeats "$TASK_REPEATS" --master-seed 0 --parallel 1 \
  --model-request-budget "$TASK_BUDGET" --per-trial-requests 400 \
  --agent-timeout 300 --max-turns 30 2>&1 | tee "$TASK_OUT/controller.log" || {
    TASK_PIPE_STATUS=("${PIPESTATUS[@]}")
    TASK_STATUS="${TASK_PIPE_STATUS[0]}"
    if [[ "$TASK_STATUS" -eq 0 ]]; then TASK_STATUS=2; fi
  }
"$TASK_PY" - "$TASK_OUT" "$TASK_STATUS" "$TASK_MODE" <<'PY'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
path = root / 'campaign.json'
campaign = json.loads(path.read_text()) if path.exists() else {}
receipt = dict(
    mode=sys.argv[3], target_version='TARGET_V1R4', exit_code=int(sys.argv[2]), retries=0,
    campaign_started=bool(campaign), controller_log='controller.log',
    scheduled_trials=len(campaign.get('schedule', [])),
    reserved_attempts=len(campaign.get('attempts', [])),
    cleanup=campaign.get('cleanup') if campaign else {'status': 'NO_CAMPAIGN_STARTED'},
    replace_invalid_trials=False,
    next_stage_started=False,
)
(root / 'manual_launch_receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
print(json.dumps(receipt, indent=2))
PY
# CPU-only derived summary and evidence export, including failures. Never retries.
if [[ -f "$TASK_OUT/campaign.json" ]]; then
  run_cli study-summary --results-dir "$TASK_OUT" || true
fi
"$TASK_PY" scripts/research_phase1/prepare_r4_review_bundle.py || {
  echo "Reviewer export failed; raw evidence remains in $TASK_OUT." >&2
  [[ "$TASK_STATUS" -ne 0 ]] || TASK_STATUS=2
}
if [[ "$TASK_STATUS" -ne 0 ]]; then
  echo "Run failed or was blocked; inspect $TASK_OUT. No retry started." >&2
fi
exit "$TASK_STATUS"
