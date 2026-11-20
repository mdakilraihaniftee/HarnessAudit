#!/usr/bin/env bash
# 04_start_server_auto.sh — start (or reuse) a local vLLM server on automatically
# chosen GPU(s): idle, enough free memory, below the utilization cap.
#
# Environment overrides:
#   HA_MODEL=Qwen/Qwen3-8B   PORT=8000   PROFILE=l40_shared
#   ALLOW_SHARED=1           # permit a partially occupied GPU (site policy!)
#   TP=auto REPLICAS=1       MAX_MODEL_LEN=32768  GPU_MEM_UTIL=0.80  MAX_NUM_SEQS=16
#   SAFETY_MARGIN_GB=6       MAX_GPU_UTIL=30      NAME=qwen-<port>
#   DRY_RUN=1                # show the placement + validated command only
# Usage:
#   scripts/local_qwen/04_start_server_auto.sh
#   ALLOW_SHARED=1 SAFETY_MARGIN_GB=8 scripts/local_qwen/04_start_server_auto.sh
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
ha_require_python
OUT="$(ha_new_run_dir server_auto)"
PORT="${PORT:-8000}"
args=(serve --gpu "${GPU_SELECTION:-auto}" --model "$HA_MODEL" --port "$PORT"
      --log-dir "$HA_RUN_ROOT/servers" --record "$OUT/launch.json")
[[ -n "${PROFILE:-l40_shared}" ]] && args+=(--profile "${PROFILE:-l40_shared}")
[[ -n "${NAME:-}" ]] && args+=(--name "$NAME")
[[ -n "${TP:-}" ]] && args+=(--tp "$TP")
[[ -n "${REPLICAS:-}" ]] && args+=(--replicas "$REPLICAS")
[[ -n "${MAX_MODEL_LEN:-}" ]] && args+=(--max-model-len "$MAX_MODEL_LEN")
[[ -n "${GPU_MEM_UTIL:-}" ]] && args+=(--gpu-memory-utilization "$GPU_MEM_UTIL")
[[ -n "${MAX_NUM_SEQS:-}" ]] && args+=(--max-num-seqs "$MAX_NUM_SEQS")
[[ -n "${SAFETY_MARGIN_GB:-}" ]] && args+=(--safety-margin-gb "$SAFETY_MARGIN_GB")
[[ -n "${MAX_GPU_UTIL:-}" ]] && args+=(--max-gpu-utilization "$MAX_GPU_UTIL")
[[ "${ALLOW_SHARED:-0}" == 1 ]] && args+=(--allow-shared-gpu)
[[ "${DRY_RUN:-0}" == 1 ]] && args+=(--dry-run)
[[ "${NO_REUSE:-0}" == 1 ]] && args+=(--no-reuse)
ha_li gpu-status > "$OUT/gpu_status_before.txt" 2>&1 || true
ha_run_logged "$OUT/serve.log" ha_li "${args[@]}"
rc=$?
if [[ $rc -eq 0 && "${DRY_RUN:-0}" != 1 ]]; then
  ha_li health --checks chat --set "inference.endpoint=http://127.0.0.1:$PORT/v1" | tee -a "$OUT/serve.log"
  ha_info "server ready on http://127.0.0.1:$PORT/v1 — launch record: $OUT/launch.json"
fi
exit $rc
