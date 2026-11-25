#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
args=(--output-dir "${OUTPUT_DIR:-results/research_phase1/pilot}")
[[ -n "${ARCHIVE:-}" ]] && args+=(--archive "$ARCHIVE")
exec "$PHASE1_PYTHON" -m multi_agent.research_phase1 package "${args[@]}" "$@"
