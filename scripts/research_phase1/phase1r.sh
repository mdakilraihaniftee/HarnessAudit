#!/usr/bin/env bash
# CPU analysis by default. GPU workloads require explicit RUN_REAL=YES.
set -euo pipefail
TASK_REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$TASK_REPO_ROOT"
export HA_INFERENCE_MODE=local_only HA_ALLOW_MODEL_DOWNLOAD=false
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
exec "$TASK_REPO_ROOT/.venv/bin/python" -m multi_agent.research_phase1r "$@"
