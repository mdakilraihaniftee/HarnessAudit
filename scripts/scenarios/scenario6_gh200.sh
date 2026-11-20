#!/usr/bin/env bash
# Scenario 6 — one GH200 node (aarch64). Requires the aarch64 envs set up once on
# the GH200 login node: rc/setup.sh && scripts/local_qwen/03_setup_qwen.sh --allow-download
# Usage: scripts/scenarios/scenario6_gh200.sh [--dry-run]
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
scripts/hpc/submit_campaign.sh --profile gh200 --script slurm_gh200_example.sbatch "$@"
