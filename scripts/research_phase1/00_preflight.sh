#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
if [[ "${COST_ONLY:-NO}" == YES ]]; then
  exec "$PHASE1_PYTHON" -m multi_agent.research_phase1 cost "$@"
fi
exec "$PHASE1_PYTHON" -m multi_agent.research_phase1 preflight --output-dir "${OUTPUT_DIR:-results/research_phase1/preflight}" "$@"
