#!/usr/bin/env bash
# 05_start_server_explicit.sh — start a server on exactly the listed GPUs
# (nvidia-smi indices). Unlisted GPUs are never used; ids outside this process's
# allocation are an error. Extra GPUs become data-parallel replicas (REPLICAS=auto)
# — never an invalid tensor-parallel group.
#
# Usage:
#   scripts/local_qwen/05_start_server_explicit.sh 0,2,3          # 3 replicas, GPU 1 untouched
#   GPU_IDS=2 scripts/local_qwen/05_start_server_explicit.sh
#   REPLICAS=1 scripts/local_qwen/05_start_server_explicit.sh 0,2,3   # one replica on the best of 0,2,3
#   TP=2 REPLICAS=1 HA_MODEL=Qwen/Qwen3-14B scripts/local_qwen/05_start_server_explicit.sh 0,2
# Other overrides as in 04_start_server_auto.sh (PORT, ALLOW_SHARED, DRY_RUN, ...).
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
ha_require_python
GPU_IDS="${1:-${GPU_IDS:-}}"
[[ "$GPU_IDS" =~ ^[0-9]+(,[0-9]+)*$ ]] || ha_die "usage: $0 GPU_IDS (e.g. 0,2,3)" 2
OUT="$(ha_new_run_dir server_explicit)"
PORT="${PORT:-8000}"
args=(serve --gpu-ids "$GPU_IDS" --model "$HA_MODEL" --port "$PORT"
      --replicas "${REPLICAS:-auto}" --log-dir "$HA_RUN_ROOT/servers" --record "$OUT/launch.json")
[[ -n "${PROFILE:-l40_shared}" ]] && args+=(--profile "${PROFILE:-l40_shared}")
[[ -n "${NAME:-}" ]] && args+=(--name "$NAME")
[[ -n "${TP:-}" ]] && args+=(--tp "$TP")
[[ -n "${MAX_MODEL_LEN:-}" ]] && args+=(--max-model-len "$MAX_MODEL_LEN")
[[ -n "${GPU_MEM_UTIL:-}" ]] && args+=(--gpu-memory-utilization "$GPU_MEM_UTIL")
[[ -n "${MAX_NUM_SEQS:-}" ]] && args+=(--max-num-seqs "$MAX_NUM_SEQS")
[[ -n "${SAFETY_MARGIN_GB:-}" ]] && args+=(--safety-margin-gb "$SAFETY_MARGIN_GB")
[[ "${ALLOW_SHARED:-0}" == 1 ]] && args+=(--allow-shared-gpu)
[[ "${DRY_RUN:-0}" == 1 ]] && args+=(--dry-run)
[[ "${NO_REUSE:-0}" == 1 ]] && args+=(--no-reuse)
ha_run_logged "$OUT/serve.log" ha_li "${args[@]}"
rc=$?
[[ $rc -eq 0 && "${DRY_RUN:-0}" != 1 ]] && ha_info "server ready on http://127.0.0.1:$PORT/v1 (GPUs $GPU_IDS) — $OUT/launch.json"
exit $rc
