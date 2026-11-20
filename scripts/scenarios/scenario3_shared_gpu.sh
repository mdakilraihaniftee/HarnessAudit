#!/usr/bin/env bash
# Scenario 3 — one partially occupied GPU that site policy allows us to share.
# Uses only GPU_ID, accepts other users' processes on it (ALLOW_SHARED), keeps
# SAFETY_MARGIN_GB free for them, and lowers gpu_memory_utilization to
# (free - margin)/total automatically. Refuses if the GPU is busier than
# MAX_GPU_UTIL % or the model does not fit. Never touches other processes.
# Usage: GPU_ID=1 scripts/scenarios/scenario3_shared_gpu.sh
set -euo pipefail
D="$(cd "$(dirname "${BASH_SOURCE[0]}")/../local_qwen" && pwd)"
source "$D/lib.sh"
GPU_ID="${GPU_ID:?set GPU_ID to the shared GPU index (see 02_gpu_status.sh)}"
ha_li plan --gpu-ids "$GPU_ID" --allow-shared-gpu --safety-margin-gb "${SAFETY_MARGIN_GB:-8}" \
  --max-gpu-utilization "${MAX_GPU_UTIL:-50}" --max-num-seqs "${MAX_NUM_SEQS:-8}"
ALLOW_SHARED=1 SAFETY_MARGIN_GB="${SAFETY_MARGIN_GB:-8}" MAX_NUM_SEQS="${MAX_NUM_SEQS:-8}" \
  REPLICAS=1 "$D/05_start_server_explicit.sh" "$GPU_ID"
"$D/06_test_inference.sh"
