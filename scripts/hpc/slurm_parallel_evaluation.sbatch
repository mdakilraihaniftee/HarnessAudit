#!/bin/bash -l
# slurm_parallel_evaluation.sbatch — *distributed experiment evaluation*.
# One task per node: each node starts its own node-local vLLM server on its
# Slurm-granted GPUs and runs an independent shard of trials. No traffic crosses
# nodes (servers bind 127.0.0.1); this is usually the most efficient way to use
# several nodes for many small-model runs.
#
#   sbatch --nodes=4 --gres=gpu:a100:4 --partition=<p> --account=<a> \
#          scripts/hpc/slurm_parallel_evaluation.sbatch
# Per node: MODE=clean|campaign, REPEAT (clean runs per node), REPLICAS (default auto =
# one vLLM data-parallel replica per granted GPU), MAX_PARALLEL_TRIALS.
#SBATCH --job-name=ha-qwen-parallel-eval
#SBATCH --nodes=2
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=32
#SBATCH --mem=128G
#SBATCH --gres=gpu:4
#SBATCH --time=12:00:00
#SBATCH --output=logs/%x_%j.out
#SBATCH --error=logs/%x_%j.err
set -euo pipefail
cd "${SLURM_SUBMIT_DIR:-.}"
mkdir -p logs
echo "nodes=${SLURM_JOB_NUM_NODES:-?} nodelist=${SLURM_JOB_NODELIST:-?}"
# srun gives every node its own step with only that node's GPUs visible.
srun --ntasks-per-node=1 --kill-on-bad-exit=0 bash -c '
  source scripts/hpc/common_hpc.sh
  export HA_RUN_ROOT="$HA_RUN_ROOT/node_$(hostname -s)"; mkdir -p "$HA_RUN_ROOT"
  hpc_banner
  trap hpc_stop_server EXIT
  hpc_start_server --replicas "${REPLICAS:-auto}"
  export CAMPAIGN_ID="node${SLURM_NODEID:-0}_${SLURM_JOB_ID:-x}"
  case "${MODE:-clean}" in
    clean)    OUT_DIR="$HA_RUN_ROOT/clean" REPEAT="${REPEAT:-4}" scripts/local_qwen/09_run_clean_finance.sh ;;
    campaign) OUT_DIR="$HA_RUN_ROOT/campaign" scripts/local_qwen/10_run_attacker_campaign.sh ;;
  esac
'
