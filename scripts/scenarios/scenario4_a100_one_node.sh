#!/usr/bin/env bash
# Scenario 4 — A100 cluster, one allocated node (run on the Slurm login node).
# Submits the A100 example job: node-local server on the granted A100(s), tests,
# ClawTeam integration test and clean fin-t1 runs. GPUS=1 by default.
# Usage: scripts/scenarios/scenario4_a100_one_node.sh  [--dry-run]
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
scripts/hpc/submit_campaign.sh --profile a100_single --gpus-per-node "${GPUS:-1}" \
  --script slurm_a100_example.sbatch "$@"
