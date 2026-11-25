#!/usr/bin/env bash
# One-command supervised local-only pilot; never sources .env or starts hosted inference.
set -euo pipefail
PHASE1_PILOT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PHASE1_PILOT_ROOT"
PHASE1_PILOT_PYTHON="${PYTHON_CMD:-$PHASE1_PILOT_ROOT/.venv/bin/python}"
[[ -x "$PHASE1_PILOT_PYTHON" ]] || { echo 'Set PYTHON_CMD to an installed HarnessAudit Python' >&2; exit 2; }
[[ "${RUN_REAL:-NO}" == YES ]] || { echo 'Set RUN_REAL=YES to authorize the explicitly budgeted real pilot' >&2; exit 2; }
[[ "${RUN_MOCK:-NO}" != YES ]] || { echo 'Mock fallback is prohibited in the real launcher' >&2; exit 2; }
export HA_INFERENCE_MODE=local_only HA_ALLOW_MODEL_DOWNLOAD=false
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
export PATH="$(dirname -- "$PHASE1_PILOT_PYTHON"):$PATH"
exec "$PHASE1_PILOT_PYTHON" -m multi_agent.research_phase1.pilot "$@"
