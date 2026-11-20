#!/bin/bash -l
# slurm_qwen_single.sbatch — single-node local Qwen serving on Slurm-granted GPUs.
# Starts a node-local vLLM server (127.0.0.1), runs health + tool-calling checks,
# then either runs HA_JOB_CMD against it or keeps serving for SERVE_SECONDS.
# Partition/account/GPU type come from the submission (see submit_campaign.sh);
# nothing site-specific is hard-coded here.
#
#   sbatch --partition=<p> --account=<a> --gres=gpu:a100:1 scripts/hpc/slurm_qwen_single.sbatch
#   HA_JOB_CMD="scripts/local_qwen/09_run_clean_finance.sh" sbatch ... slurm_qwen_single.sbatch
#SBATCH --job-name=ha-qwen-serve
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=64G
#SBATCH --gres=gpu:1
#SBATCH --time=04:00:00
#SBATCH --output=logs/%x_%j.out
#SBATCH --error=logs/%x_%j.err
source "${SLURM_SUBMIT_DIR:-.}/scripts/hpc/common_hpc.sh"
hpc_banner
trap hpc_stop_server EXIT
hpc_start_server ${HA_SERVE_ARGS:-}
ha_li health --out "$HA_RUN_ROOT/health.json"
ha_li test-tools --out "$HA_RUN_ROOT/test_tools.json"
ha_li verify-cost --out "$HA_RUN_ROOT/verify_cost.json"
if [[ -n "${HA_JOB_CMD:-}" ]]; then
  echo "+ $HA_JOB_CMD"
  bash -c "$HA_JOB_CMD"
else
  echo "serving on $HA_LOCAL_ENDPOINT for ${SERVE_SECONDS:-3600}s (node-local only)"
  sleep "${SERVE_SECONDS:-3600}"
fi
