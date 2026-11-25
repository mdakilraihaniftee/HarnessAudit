#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
exec "$PHASE1_PYTHON" -m multi_agent.research_phase1 analyze --output-dir "${OUTPUT_DIR:-results/research_phase1/pilot}" "$@"
