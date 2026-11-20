#!/usr/bin/env bash
# 12_stop_own_server.sh — stop local servers *this user started through these tools*.
# Only process groups recorded in our state files, owned by this user and running
# vLLM are signalled. Other users' processes are never touched.
# Usage:
#   scripts/local_qwen/12_stop_own_server.sh            # stop qwen-8000
#   scripts/local_qwen/12_stop_own_server.sh qwen-8001
#   scripts/local_qwen/12_stop_own_server.sh --all      # all of *our* recorded servers
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
ha_require_python
ha_li status
if [[ "${1:-}" == --all ]]; then ha_li stop --all; else ha_li stop --name "${1:-qwen-${PORT:-8000}}"; fi
sleep 3
ha_li gpu-status
