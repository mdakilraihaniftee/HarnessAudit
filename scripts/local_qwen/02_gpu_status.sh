#!/usr/bin/env bash
# 02_gpu_status.sh — GPUs on this node, their load, and which ones we may use.
# Usage: scripts/local_qwen/02_gpu_status.sh [--json]
#        PROFILE=l40_shared scripts/local_qwen/02_gpu_status.sh
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
ha_require_python
args=("$@")
[[ -n "${PROFILE:-}" ]] && args+=(--profile "$PROFILE")
ha_li gpu-status "${args[@]}"
