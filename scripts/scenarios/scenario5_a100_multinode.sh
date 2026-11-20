#!/usr/bin/env bash
# Scenario 5 — A100 cluster with 2, 4 or 6 allocated nodes (distributed evaluation).
# Each node runs its own server (one replica per granted GPU) and REPEAT clean runs
# (MODE=clean) or a campaign (MODE=campaign). NODELIST pins named nodes.
# Usage: NODES=4 scripts/scenarios/scenario5_a100_multinode.sh [--dry-run]
#        NODES=2 NODELIST=gpu03,gpu07 scripts/scenarios/scenario5_a100_multinode.sh
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
NODES="${NODES:-2}"
[[ "$NODES" =~ ^(2|4|6)$ ]] || echo "note: NODES=$NODES (scenario targets 2, 4 or 6)" >&2
args=(--profile a100_multinode --nodes "$NODES" --gpus-per-node "${GPUS_PER_NODE:-4}"
      --script slurm_parallel_evaluation.sbatch --mode "${MODE:-clean}" --repeat "${REPEAT:-4}")
[[ -n "${NODELIST:-}" ]] && args+=(--nodelist "$NODELIST")
scripts/hpc/submit_campaign.sh "${args[@]}" "$@"
