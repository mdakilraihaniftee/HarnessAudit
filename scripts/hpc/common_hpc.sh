# shellcheck shell=bash
# scripts/hpc/common_hpc.sh — sourced by the sbatch scripts (inside an allocation).
#
# * Activates the HarnessAudit env: the rc kit venv (~/venvs/HarnessAudit-<cluster>)
#   when present, else the repo .venv. vLLM comes from the arch-specific venv
#   ~/venvs/HarnessAudit-vllm-<arch> (x86_64 for A100, aarch64 for GH200).
# * Uses only GPUs Slurm granted (gpu.selection=scheduler); never nvidia-smi probing
#   of unallocated GPUs.
# * Per-job scratch on node-local storage; model server bound to 127.0.0.1 unless
#   a multi-node script explicitly needs otherwise.

set -euo pipefail
HA_ROOT="${SLURM_SUBMIT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
[[ -f "$HA_ROOT/scripts/local_qwen/lib.sh" ]] || HA_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$HA_ROOT"

if [[ -z "${HA_PYTHON:-}" && -f rc/common.sh && "${HA_USE_RC:-1}" == 1 ]]; then
  # rc kit: cluster detection, shared HF cache, per-arch venv. rc_activate refuses a stale env.
  # shellcheck source=/dev/null
  source rc/common.sh
  if [[ -x "$VENV/bin/python" ]]; then
    rc_activate
    export HA_PYTHON="$VENV/bin/python"
  fi
fi
# shellcheck source=../local_qwen/lib.sh
source "$HA_ROOT/scripts/local_qwen/lib.sh"
ha_require_python

export HA_INFERENCE_MODE="${HA_INFERENCE_MODE:-local_only}"
export HA_CLUSTER_PROFILE="${HA_CLUSTER_PROFILE:-${PROFILE:-}}"
JOB_TAG="${SLURM_JOB_ID:-nojob}${SLURM_ARRAY_TASK_ID:+_$SLURM_ARRAY_TASK_ID}"
export HARNESSAUDIT_JOB_TMP="${HARNESSAUDIT_JOB_TMP:-${TMPDIR:-/tmp}/harnessaudit_${JOB_TAG}_$(hostname -s)}"
export CLAWTEAM_DATA_DIR="$HARNESSAUDIT_JOB_TMP/clawteam"
export MASP_WORKSPACES_ROOT="$HARNESSAUDIT_JOB_TMP/workspaces"
export MASP_QWEN_ISOLATION_ROOT="$HARNESSAUDIT_JOB_TMP/qwen"
export HA_GPU_LOCK_DIR="$HARNESSAUDIT_JOB_TMP/gpu_locks"     # job-private: GPUs are ours
export HA_SERVER_STATE_DIR="$HARNESSAUDIT_JOB_TMP/servers"
mkdir -p "$CLAWTEAM_DATA_DIR" "$MASP_WORKSPACES_ROOT" "$MASP_QWEN_ISOLATION_ROOT"
export HA_RUN_ROOT="${HA_RUN_ROOT:-$HA_ROOT/results/local_qwen/slurm_${JOB_TAG}}"
mkdir -p "$HA_RUN_ROOT"

hpc_banner() {
  echo "================ HarnessAudit local-Qwen job ================"
  echo "job      : ${SLURM_JOB_ID:-none} ${SLURM_JOB_NAME:-} array=${SLURM_ARRAY_TASK_ID:-none}"
  echo "node     : $(hostname) [$(uname -m)] node_id=${SLURM_NODEID:-0} local_id=${SLURM_LOCALID:-0}"
  echo "nodes    : count=${SLURM_JOB_NUM_NODES:-1} list=${SLURM_JOB_NODELIST:-$(hostname)}"
  echo "gpus     : CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES:-unset} SLURM_JOB_GPUS=${SLURM_JOB_GPUS:-unset} SLURM_STEP_GPUS=${SLURM_STEP_GPUS:-unset}"
  echo "profile  : ${HA_CLUSTER_PROFILE:-none}   mode: $HA_INFERENCE_MODE"
  echo "python   : $HA_PYTHON"
  echo "vllm     : $HA_VLLM_VENV"
  echo "results  : $HA_RUN_ROOT"
  echo "commit   : $(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
  echo "============================================================="
  ha_li gpu-status --set gpu.selection=scheduler || true
}

hpc_free_port() {
  "$HA_PYTHON" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()'
}

# hpc_start_server [extra serve args...] -> exports HA_LOCAL_ENDPOINT, HPC_SERVER_NAME
hpc_start_server() {
  local port="${HA_SERVER_PORT:-$(hpc_free_port)}"
  HPC_SERVER_NAME="job${JOB_TAG}-$(hostname -s)-$port"
  export HA_SERVER_PORT="$port" HA_LOCAL_ENDPOINT="http://127.0.0.1:$port/v1" HPC_SERVER_NAME
  ha_li serve --gpu scheduler --port "$port" --name "$HPC_SERVER_NAME" --no-reuse \
    --log-dir "$HA_RUN_ROOT/servers" --record "$HA_RUN_ROOT/launch_$(hostname -s).json" "$@"
}

hpc_stop_server() {
  [[ -n "${HPC_SERVER_NAME:-}" ]] && ha_li stop --name "$HPC_SERVER_NAME" || true
  rm -rf "$HARNESSAUDIT_JOB_TMP"
}
