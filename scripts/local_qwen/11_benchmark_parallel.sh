#!/usr/bin/env bash
# 11_benchmark_parallel.sh — serving benchmarks across GPU layouts.
#
# Configurations (CONFIGS, space separated; unavailable layouts are SKIPPED):
#   tp1_auto      1 GPU chosen automatically
#   tp1_explicit  1 GPU given by EXPLICIT_GPU (default 3)
#   tp2 / tp4     one model sharded with tensor parallelism over 2 / 4 GPUs
#   dp2 / dp4     vLLM data parallel: 2 / 4 full replicas behind one endpoint
#   sep2          two independent TP=1 servers, client-side round robin
# For each: CONCURRENCY levels (default 1,4,8,16), REQUESTS per level (default 4x conc),
# PROMPT_TOKENS (1500) and MAX_TOKENS (256, ignore_eos so lengths are fixed).
#
# Safety: refuses to run if any GPU has other users' processes unless FORCE=1
# (no stress tests on shared hardware in use). Only servers started here are stopped.
# Usage:
#   scripts/local_qwen/11_benchmark_parallel.sh
#   CONFIGS="tp1_auto dp2" CONCURRENCY=1,4 scripts/local_qwen/11_benchmark_parallel.sh
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
ha_require_python
CONFIGS="${CONFIGS:-tp1_auto tp1_explicit tp2 tp4 dp2 dp4 sep2}"
CONCURRENCY="${CONCURRENCY:-1,4,8,16}"
OUT="$(ha_new_run_dir benchmark)"
busy=$(ha_li gpu-status --json | "$HA_PYTHON" -c '
import json,sys; d=json.load(sys.stdin)
print(sum(1 for g in d["devices"] if g["foreign_process_count"] > 0))')
if [[ "$busy" != 0 && "${FORCE:-0}" != 1 ]]; then
  ha_li gpu-status
  ha_die "$busy GPU(s) are in use by other users; not running stress benchmarks (FORCE=1 overrides)" 3
fi
STARTED=()
stop_started() { for n in "${STARTED[@]:-}"; do [[ -n "$n" ]] && ha_li stop --name "$n" >/dev/null || true; done; STARTED=(); }
trap stop_started EXIT
port=8100
for cfg in $CONFIGS; do
  port=$((port + 2))
  names=(); endpoints=(); serve_extra=()
  case "$cfg" in
    tp1_auto)     serve_extra=(--gpu auto --tp 1 --replicas 1) ;;
    tp1_explicit) serve_extra=(--gpu-ids "${EXPLICIT_GPU:-3}" --tp 1 --replicas 1) ;;
    tp2)          serve_extra=(--gpu auto --tp 2 --replicas 1) ;;
    tp4)          serve_extra=(--gpu auto --tp 4 --replicas 1) ;;
    dp2)          serve_extra=(--gpu auto --tp 1 --replicas 2) ;;
    dp4)          serve_extra=(--gpu auto --tp 1 --replicas 4) ;;
    sep2)         serve_extra=(--gpu auto --tp 1 --replicas 1) ;;
    *) ha_info "unknown config $cfg"; continue ;;
  esac
  count=1; [[ "$cfg" == sep2 ]] && count=2
  ok=1
  for k in $(seq 1 $count); do
    p=$((port + k - 1)); n="bench-$cfg-$k"
    if ha_li serve "${serve_extra[@]}" --port "$p" --name "$n" --no-reuse \
        --log-dir "$OUT/server_logs" --record "$OUT/$cfg.launch$k.json" > "$OUT/$cfg.serve$k.log" 2>&1; then
      STARTED+=("$n"); names+=("$n"); endpoints+=("http://127.0.0.1:$p/v1")
    else
      ok=0; ha_info "SKIPPED $cfg: $(grep -E 'SERVER FAILED|ERROR' "$OUT/$cfg.serve$k.log" | head -2 | tr '\n' ' ')"
      echo "{\"config\": \"$cfg\", \"status\": \"SKIPPED\"}" > "$OUT/$cfg.skipped.json"
      break
    fi
  done
  if (( ok )); then
    gpus=$("$HA_PYTHON" -c '
import glob, json, sys
idx = []
for path in sorted(glob.glob(sys.argv[1] + "/" + sys.argv[2] + ".launch*.json")):
    plan = json.load(open(path)).get("plan") or {}
    idx += [d["nvidia_smi_index"] for d in plan.get("device_map", [])]
print(",".join(str(i) for i in idx))' "$OUT" "$cfg")
    startup=$("$HA_PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1])).get("startup_s"))' "$OUT/$cfg.launch1.json")
    ha_info "benchmark $cfg on GPUs $gpus (startup ${startup}s) endpoints ${endpoints[*]}"
    ha_li benchmark --endpoints "$(IFS=,; echo "${endpoints[*]}")" --concurrency "$CONCURRENCY" \
      ${REQUESTS:+--requests "$REQUESTS"} --prompt-tokens "${PROMPT_TOKENS:-1500}" \
      --max-tokens "${MAX_TOKENS:-256}" --gpu-indices "$gpus" --label "$cfg" --out "$OUT/$cfg" \
      | tee "$OUT/$cfg.benchmark.txt" || true
    echo "startup_s=$startup gpus=$gpus" >> "$OUT/$cfg.benchmark.txt"
  fi
  stop_started
  sleep 5
done
"$HA_PYTHON" "$HA_ROOT/scripts/local_qwen/report_benchmarks.py" "$OUT" | tee "$OUT/summary.md"
ha_info "benchmark artifacts: $OUT"
