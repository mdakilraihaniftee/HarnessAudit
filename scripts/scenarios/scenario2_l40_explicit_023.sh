#!/usr/bin/env bash
# Scenario 2 — 4x L40, use ONLY GPUs 0,2,3 and never GPU 1.
# Qwen3-8B fits on one GPU, so the three GPUs become three data-parallel replicas
# behind one endpoint (no invalid TP=3 split). Afterwards it proves GPU 1 hosts
# none of our processes.
# Usage: scripts/scenarios/scenario2_l40_explicit_023.sh   (GPU_IDS=0,2,3 by default)
set -euo pipefail
D="$(cd "$(dirname "${BASH_SOURCE[0]}")/../local_qwen" && pwd)"
source "$D/lib.sh"
GPU_IDS="${GPU_IDS:-0,2,3}"
REPLICAS="${REPLICAS:-auto}" "$D/05_start_server_explicit.sh" "$GPU_IDS"
"$D/06_test_inference.sh"
mine=$(ha_li gpu-status --json | "$HA_PYTHON" -c '
import json,sys,os
d=json.load(sys.stdin); allowed=set(int(x) for x in sys.argv[1].split(","))
bad=[g["index"] for g in d["devices"] if g["index"] not in allowed and any(p.get("own") for p in g["processes"])]
print(",".join(map(str,bad)))' "$GPU_IDS")
[[ -z "$mine" ]] && echo "PASS: no process of ours on GPUs outside $GPU_IDS" || { echo "FAIL: our processes on GPU(s) $mine"; exit 1; }
