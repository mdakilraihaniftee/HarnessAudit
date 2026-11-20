#!/bin/bash -l
# slurm_a100_example.sbatch — one A100 (40 or 80 GB, detected at run time) node: start a node-local
# Qwen server on the Slurm-granted GPU, run inference + tool-calling + cost checks,
# the real ClawTeam integration test, then clean fin-t1 runs.
# Submit with the profile defaults (partition/account from configs/cluster/a100_single.yaml):
#   scripts/hpc/submit_campaign.sh --profile a100_single --script slurm_a100_example.sbatch
#SBATCH --job-name=ha-qwen-a100
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=64G
#SBATCH --gres=gpu:1
#SBATCH --time=04:00:00
#SBATCH --output=logs/%x_%j.out
#SBATCH --error=logs/%x_%j.err
export PROFILE="${PROFILE:-a100_single}"
source "${SLURM_SUBMIT_DIR:-.}/scripts/hpc/common_hpc.sh"
hpc_banner
trap hpc_stop_server EXIT
hpc_start_server ${HA_SERVE_ARGS:-}
"$HA_ROOT/scripts/local_qwen/06_test_inference.sh"
"$HA_ROOT/scripts/local_qwen/07_test_tool_calls.sh"
"$HA_ROOT/scripts/local_qwen/00_check_costs.sh"
"$HA_ROOT/scripts/local_qwen/08_test_clawteam.sh" || echo "integration test reported failures (see verdict)"
REPEAT="${REPEAT:-2}" "$HA_ROOT/scripts/local_qwen/09_run_clean_finance.sh"
