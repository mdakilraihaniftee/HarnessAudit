#!/usr/bin/env bash
# Scenario 7 — a local Qwen server is already running: reuse it for a three-round
# SI-ATG campaign without loading another model copy. Fails if no healthy server
# exists (start one with scenario 1/2) and verifies afterwards that the number of
# our vLLM servers and GPU-resident processes did not change.
# Usage: scripts/scenarios/scenario7_reuse_server_campaign.sh   (ROUNDS=3 by default)
set -euo pipefail
D="$(cd "$(dirname "${BASH_SOURCE[0]}")/../local_qwen" && pwd)"
source "$D/lib.sh"
ha_li health --checks chat >/dev/null || ha_die "no healthy local server on $(ha_cfg inference.endpoint); start one first" 2
count_gpu_procs() { ha_li gpu-status --json | "$HA_PYTHON" -c 'import json,sys; print(sum(len([p for p in g["processes"] if p.get("own")]) for g in json.load(sys.stdin)["devices"]))'; }
before=$(count_gpu_procs)
ROUNDS="${ROUNDS:-3}" START_SERVER=0 "$D/10_run_attacker_campaign.sh"
after=$(count_gpu_procs)
echo "our GPU processes before=$before after=$after"
[[ "$before" == "$after" ]] && echo "PASS: campaign reused the existing server (no duplicate model copy)" \
  || { echo "FAIL: GPU process count changed"; exit 1; }
