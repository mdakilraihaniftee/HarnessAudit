#!/usr/bin/env bash
# Scenario 1 — 4x L40 workstation, automatically choose ONE suitable GPU.
# Picks an idle GPU with enough free memory (never a busy one), starts Qwen3-8B,
# verifies inference, tool calling and cost safety. RUN_TASK=1 also runs fin-t1.
# Usage: scripts/scenarios/scenario1_l40_auto_one_gpu.sh
set -euo pipefail
D="$(cd "$(dirname "${BASH_SOURCE[0]}")/../local_qwen" && pwd)"
"$D/02_gpu_status.sh"
PROFILE=l40_shared TP=1 REPLICAS=1 "$D/04_start_server_auto.sh"
"$D/00_check_costs.sh"
"$D/07_test_tool_calls.sh"
[[ "${RUN_TASK:-0}" == 1 ]] && "$D/09_run_clean_finance.sh"
echo "Scenario 1 done. Stop the server with: scripts/local_qwen/12_stop_own_server.sh"
