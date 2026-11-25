#!/usr/bin/env bash
# Exactly one manually invoked, frozen TARGET_V1R2 smoke; never resumes or retries.
set -euo pipefail
TASK_REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$TASK_REPO_ROOT"
TASK_SMOKE_DIR=results/research_phase1r/diagnostic_smoke/v3
TASK_RELEASE_DIR=results/research_phase1r/target_v1r2
if [[ -e "$TASK_SMOKE_DIR" ]]; then
  echo "Refusing to replace $TASK_SMOKE_DIR; preserve the existing evidence." >&2
  exit 2
fi
if [[ ! -f "$TASK_RELEASE_DIR/TARGET_V1R2.json" ]]; then
  echo "Missing frozen TARGET_V1R2 release; run the documented CPU preparation first." >&2
  exit 2
fi
export GPU_IDS=0,1,2,3 RUN_REAL=YES
export HA_INFERENCE_MODE=local_only HA_ALLOW_MODEL_DOWNLOAD=false
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
# CLI values override ambient experiment settings. CUDA/Slurm allocation restrictions
# remain inherited and are checked; no model download or paid fallback is permitted.
mkdir -p "${TASK_SMOKE_DIR%/*}"
mkdir "$TASK_SMOKE_DIR"
TASK_SMOKE_STATUS=0
bash scripts/research_phase1/phase1r.sh smoke \
  --target-version TARGET_V1R2 \
  --target-manifest "$TASK_RELEASE_DIR/TARGET_V1R2.json" \
  --results-dir "$TASK_SMOKE_DIR" --gpu-ids 0,1,2,3 \
  --server-port 8000 --proxy-port 18081 \
  --repeats 1 --master-seed 0 --parallel 1 \
  --model-request-budget 1200 --per-trial-requests 400 \
  --agent-timeout 300 --max-turns 30 2>&1 | tee "$TASK_SMOKE_DIR/controller.log" || {
    TASK_PIPE_STATUS=("${PIPESTATUS[@]}")
    TASK_SMOKE_STATUS="${TASK_PIPE_STATUS[0]}"
    if [[ "$TASK_SMOKE_STATUS" -eq 0 ]]; then TASK_SMOKE_STATUS=2; fi
  }
"$TASK_REPO_ROOT/.venv/bin/python" - "$TASK_SMOKE_DIR" "$TASK_SMOKE_STATUS" <<'PY'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
campaign_path = root / "campaign.json"
campaign = json.loads(campaign_path.read_text()) if campaign_path.exists() else {}
receipt = dict(
    smoke_exit_code=int(sys.argv[2]),
    campaign_started=bool(campaign),
    controller_log="controller.log",
    cleanup=campaign.get("cleanup") if campaign else {"status": "NO_CAMPAIGN_STARTED"},
    retries=0,
)
(root / "manual_launch_receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
PY
# Refresh the root reviewer archive with any new evidence, including failures.
# This is CPU packaging only; it cannot authorize or launch a subsequent campaign.
bash scripts/research_phase1/phase1r.sh package \
  --results-dir results/research_phase1r || {
    echo "Reviewer packaging failed; raw smoke evidence remains in $TASK_SMOKE_DIR." >&2
    exit 2
  }
if [[ "$TASK_SMOKE_STATUS" -ne 0 ]]; then
  echo "Smoke failed or was blocked. Inspect smoke_diagnosis.json and logs; no retry was started." >&2
fi
exit "$TASK_SMOKE_STATUS"
