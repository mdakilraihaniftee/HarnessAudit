#!/usr/bin/env bash
# test_gpu_selection.sh — GPU placement through the real `plan` CLI on mocked NVML
# snapshots (no GPUs touched), plus the pytest placement suite.
# Usage: scripts/tests/test_gpu_selection.sh
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../local_qwen/lib.sh"
ha_require_python
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
export HA_GPU_LOCK_DIR="$TMP/locks" HA_SERVER_STATE_DIR="$TMP/servers"
unset CUDA_VISIBLE_DEVICES SLURM_JOB_ID SLURM_STEP_GPUS SLURM_JOB_GPUS HA_GPU_IDS
mk() {  # mk FILE "idx:used_gb:util:foreign" ...
  local f="$1"; shift
  "$HA_PYTHON" - "$f" "$@" <<'PY'
import json, sys
devs = []
for spec in sys.argv[2:]:
    i, used, util, foreign = spec.split(":")
    used = float(used)
    devs.append({"index": int(i), "uuid": f"GPU-{i}", "name": "NVIDIA L40S", "total_gb": 45.0,
                 "free_gb": 45.0 - used, "used_gb": used, "utilization_pct": float(util),
                 "compute_capability": "8.9", "mig_enabled": False,
                 "processes": [{"pid": 999999, "used_memory_gb": used, "uid": 4242, "own": False}]
                 if foreign == "1" else []})
json.dump({"host": "mock", "devices": devs}, open(sys.argv[1], "w"))
PY
}
pass=0; fail=0
expect() {  # expect NAME PATTERN -- plan args...
  local name="$1" pattern="$2"; shift 3
  local out; out=$(ha_li plan "$@" 2>&1)
  if grep -Eq -- "$pattern" <<<"$out"; then echo "PASS  $name"; pass=$((pass+1));
  else echo "FAIL  $name (wanted /$pattern/)"; echo "$out" | tail -4 | sed 's/^/      /'; fail=$((fail+1)); fi
}
mk "$TMP/idle4.json" 0:0:0:0 1:0:0:0 2:0:0:0 3:0:0:0
mk "$TMP/busy.json" 0:30:60:1 1:35:90:1 2:0:0:0 3:20:10:1
mk "$TMP/allbusy.json" 0:40:95:1 1:41:95:1 2:42:99:1 3:40:97:1
mk "$TMP/shared.json" 0:12:10:1
HA_GPU_MOCK_FILE="$TMP/idle4.json" expect "4 idle GPUs -> one GPU for an 8B model" "TP=1 x replicas=1" --
HA_GPU_MOCK_FILE="$TMP/busy.json"  expect "GPU0 busy, GPU2 free -> GPU2" "CUDA_VISIBLE_DEVICES=2 " --
HA_GPU_MOCK_FILE="$TMP/allbusy.json" expect "all busy -> refuse" "ERROR" -- --allow-shared-gpu
HA_GPU_MOCK_FILE="$TMP/idle4.json" expect "explicit 0,2,3 -> replicas on 0,2,3 only" "CUDA_VISIBLE_DEVICES=0,2,3 " -- --gpu-ids 0,2,3 --replicas auto
HA_GPU_MOCK_FILE="$TMP/idle4.json" expect "explicit TP=3 rejected" "invalid for Qwen/Qwen3-8B" -- --gpu-ids 0,2,3 --tp 3
HA_GPU_MOCK_FILE="$TMP/shared.json" expect "occupied GPU refused by default" "ERROR" --
HA_GPU_MOCK_FILE="$TMP/shared.json" expect "occupied GPU allowed with --allow-shared-gpu" "PLAN: TP=1" -- --allow-shared-gpu
HA_GPU_MOCK_FILE="$TMP/idle4.json" CUDA_VISIBLE_DEVICES=1,3 expect "CUDA_VISIBLE_DEVICES restricts" "CUDA_VISIBLE_DEVICES=(1|3) " --
HA_GPU_MOCK_FILE="$TMP/idle4.json" CUDA_VISIBLE_DEVICES=1,3 expect "explicit id outside allocation refused" "not permitted" -- --gpu-ids 0
HA_GPU_MOCK_FILE="$TMP/idle4.json" SLURM_JOB_ID=1 SLURM_STEP_GPUS=2 expect "Slurm grants only GPU 2" "CUDA_VISIBLE_DEVICES=2 " -- --gpu scheduler
HA_GPU_MOCK_FILE="$TMP/idle4.json" SLURM_JOB_ID=1 expect "Slurm job without GPUs refused" "no GPUs" -- --gpu scheduler
HA_GPU_MOCK_FILE="$TMP/idle4.json" expect "Qwen3-14B on 45 GB fits TP=1" "TP=1" -- --model Qwen/Qwen3-14B
HA_GPU_MOCK_FILE="$TMP/idle4.json" expect "Qwen3-32B needs TP>1" "TP=(2|4)" -- --model Qwen/Qwen3-32B
"$HA_PYTHON" -m pytest -q "$HA_ROOT/tests/test_local_inference.py" -k "gpu or placement or slurm or mig or oom or cuda or idle or occupied or explicit or memory or race or stale" \
  && { echo "PASS  pytest placement suite"; pass=$((pass+1)); } || { echo "FAIL  pytest placement suite"; fail=$((fail+1)); }
echo "gpu selection: $pass passed, $fail failed"
(( fail == 0 ))
