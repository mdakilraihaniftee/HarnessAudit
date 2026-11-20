#!/bin/bash -l
# slurm_qwen_multinode.sbatch — *distributed inference*: one model spanning nodes.
# Tensor parallel inside each node (TP = GPUs per node), pipeline parallel across
# nodes (PP = number of nodes), vLLM's Ray executor. Ray is started entirely
# inside this allocation with srun (no SSH, no services outside the job).
#
# Use this only when the model does not fit on one node; for many independent
# trials with a small model use slurm_parallel_evaluation.sbatch instead.
# Requirements: Ray in the vLLM venv (03_setup_qwen.sh --allow-download --only ray),
# model weights on a filesystem every node can read (shared HF_HUB_CACHE), and
# Ray/NCCL traffic permitted between allocated nodes by site policy.
#
#   sbatch --nodes=2 --gres=gpu:a100:4 --partition=<p> --account=<a> \
#     --export=ALL,HA_MODEL=Qwen/Qwen3-32B scripts/hpc/slurm_qwen_multinode.sbatch
# NETWORK_INTERFACE (e.g. ib0) pins NCCL/Gloo to the cluster fabric.
#SBATCH --job-name=ha-qwen-multinode
#SBATCH --nodes=2
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=32
#SBATCH --mem=128G
#SBATCH --gres=gpu:4
#SBATCH --exclusive
#SBATCH --time=04:00:00
#SBATCH --output=logs/%x_%j.out
#SBATCH --error=logs/%x_%j.err
source "${SLURM_SUBMIT_DIR:-.}/scripts/hpc/common_hpc.sh"
hpc_banner
"$HA_VLLM_VENV/bin/python" -c 'import ray' 2>/dev/null \
  || ha_die "Ray missing in $HA_VLLM_VENV; run scripts/local_qwen/03_setup_qwen.sh --allow-download --only ray" 2
mapfile -t NODES < <(scontrol show hostnames "$SLURM_JOB_NODELIST")
NNODES=${#NODES[@]}
HEAD=${NODES[0]}
GPN=$(echo "${CUDA_VISIBLE_DEVICES:-0}" | tr ',' '\n' | grep -c .)
(( NNODES >= 2 )) || ha_die "multi-node inference needs >= 2 nodes (got $NNODES); use slurm_qwen_single.sbatch" 2
HEAD_IP=$(srun --nodes=1 --ntasks=1 -w "$HEAD" hostname --ip-address | awk '{print $1}')
RAY_PORT=$((20000 + SLURM_JOB_ID % 20000))
PORT="${HA_SERVER_PORT:-$(hpc_free_port)}"
if [[ -n "${NETWORK_INTERFACE:-}" ]]; then
  export NCCL_SOCKET_IFNAME="$NETWORK_INTERFACE" GLOO_SOCKET_IFNAME="$NETWORK_INTERFACE"
fi
export VLLM_NO_USAGE_STATS=1 DO_NOT_TRACK=1 HF_HUB_OFFLINE=1 RAY_USAGE_STATS_ENABLED=0
RAY="$HA_VLLM_VENV/bin/ray"
echo "head=$HEAD ($HEAD_IP) nodes=$NNODES gpus/node=$GPN -> TP=$GPN PP=$NNODES ray_port=$RAY_PORT"

# Validate the model fits and the flags exist (placement for one node's share).
ha_li serve --dry-run --gpu scheduler --tp "$GPN" --replicas 1 --port "$PORT" --no-reuse \
  --set inference.pipeline_parallel_size="$NNODES" | tee "$HA_RUN_ROOT/plan.txt"

cleanup() {
  srun --overlap --nodes="$NNODES" --ntasks="$NNODES" "$RAY" stop --force >/dev/null 2>&1 || true
  [[ -n "${VLLM_PID:-}" ]] && kill "$VLLM_PID" 2>/dev/null || true
  rm -rf "$HARNESSAUDIT_JOB_TMP"
}
trap cleanup EXIT
srun --overlap --nodes=1 --ntasks=1 -w "$HEAD" "$RAY" start --head --node-ip-address="$HEAD_IP" \
  --port="$RAY_PORT" --num-gpus="$GPN" --include-dashboard=false --disable-usage-stats --block &
sleep 15
for node in "${NODES[@]:1}"; do
  srun --overlap --nodes=1 --ntasks=1 -w "$node" "$RAY" start --address="$HEAD_IP:$RAY_PORT" \
    --num-gpus="$GPN" --disable-usage-stats --block &
done
sleep 20
srun --overlap --nodes=1 --ntasks=1 -w "$HEAD" "$RAY" status --address="$HEAD_IP:$RAY_PORT" | tee "$HA_RUN_ROOT/ray_status.txt"

MODEL_PATH=$("$HA_VLLM_VENV/bin/python" -c "from huggingface_hub import snapshot_download as s; print(s('$HA_MODEL', local_files_only=True))")
# The API server binds to loopback on the head node: clients run in this job, on the head.
"$HA_VLLM_VENV/bin/vllm" serve "$MODEL_PATH" --served-model-name "$HA_MODEL" \
  --host 127.0.0.1 --port "$PORT" --tensor-parallel-size "$GPN" --pipeline-parallel-size "$NNODES" \
  --distributed-executor-backend ray --max-model-len "${MAX_MODEL_LEN:-32768}" \
  --gpu-memory-utilization "${GPU_MEM_UTIL:-0.90}" --enable-auto-tool-choice --tool-call-parser hermes \
  --reasoning-parser qwen3 --default-chat-template-kwargs '{"enable_thinking": false}' \
  > "$HA_RUN_ROOT/vllm_multinode.log" 2>&1 &
VLLM_PID=$!
export HA_LOCAL_ENDPOINT="http://127.0.0.1:$PORT/v1"
for _ in $(seq 1 180); do
  curl -sf "$HA_LOCAL_ENDPOINT/models" >/dev/null && break
  kill -0 "$VLLM_PID" 2>/dev/null || { tail -50 "$HA_RUN_ROOT/vllm_multinode.log"; ha_die "vLLM exited" 3; }
  sleep 10
done
ha_li health --out "$HA_RUN_ROOT/health.json"
ha_li test-tools --out "$HA_RUN_ROOT/test_tools.json"
if [[ -n "${HA_JOB_CMD:-}" ]]; then bash -c "$HA_JOB_CMD"; else sleep "${SERVE_SECONDS:-1800}"; fi
