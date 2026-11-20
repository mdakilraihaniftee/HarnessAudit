#!/bin/bash -l
# slurm_finance_campaign.sbatch — one SI-ATG campaign (or clean runs) on one node.
# Node-local vLLM server on the job's GPU(s), Qwen Code attacker + ClawTeam agents.
# Job arrays run independent campaigns (one per array index, separate memory DBs):
#   sbatch --array=0-3 ... scripts/hpc/slurm_finance_campaign.sbatch
# Environment (forwarded by sbatch): ROUNDS ARM BACKEND BASELINE_RUNS JUDGE
#   MODE=campaign|clean  REPEAT (clean)  AGENT_TIMEOUT  MAX_WALL_TIME
#SBATCH --job-name=ha-qwen-campaign
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=64G
#SBATCH --gres=gpu:1
#SBATCH --time=08:00:00
#SBATCH --output=logs/%x_%A_%a.out
#SBATCH --error=logs/%x_%A_%a.err
source "${SLURM_SUBMIT_DIR:-.}/scripts/hpc/common_hpc.sh"
hpc_banner
trap hpc_stop_server EXIT
hpc_start_server ${HA_SERVE_ARGS:-}
export CAMPAIGN_ID="${CAMPAIGN_ID:-slurm${SLURM_ARRAY_JOB_ID:-${SLURM_JOB_ID:-x}}_${SLURM_ARRAY_TASK_ID:-0}}"
case "${MODE:-campaign}" in
  campaign) OUT_DIR="$HA_RUN_ROOT/campaign" "$HA_ROOT/scripts/local_qwen/10_run_attacker_campaign.sh" ;;
  clean)    OUT_DIR="$HA_RUN_ROOT/clean" "$HA_ROOT/scripts/local_qwen/09_run_clean_finance.sh" ;;
  *) ha_die "MODE must be campaign or clean" 2 ;;
esac
