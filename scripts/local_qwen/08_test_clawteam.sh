#!/usr/bin/env bash
# 08_test_clawteam.sh — real ClawTeam + Qwen Code integration test.
# Runs a synthetic 2-agent finance workflow (tests/fixtures/synthetic/syn-mailbox.yaml;
# TASK=... to use another task, e.g. fin-t1) with local qwen_code agents — real
# processes, real MCP tools, no mocks or fake traces — and
# verifies from the run's own artifacts that: coordinator and specialists started,
# role instructions were delivered, task-board and mailbox calls worked, finance
# MCP tools were called and returned results, native traces were collected and
# normalized, the checker evaluated them, and all network egress was loopback.
# Usage: scripts/local_qwen/08_test_clawteam.sh
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
ha_require_python
OUT="$(ha_new_run_dir test_clawteam)"
set +e
TASK="${TASK:-tests/fixtures/synthetic/syn-mailbox.yaml}" OUT_DIR="$OUT" REPEAT=1 JUDGE=0 \
  AGENT_TIMEOUT="${AGENT_TIMEOUT:-600}" "$HA_ROOT/scripts/local_qwen/09_run_clean_finance.sh"
run_rc=$?
set -e
result=$(find "$OUT/results" -name '*.json' -newer "$OUT/run_1.log" -o -name '*.json' 2>/dev/null | head -1)
[[ -n "$result" ]] || ha_die "no result JSON produced (run exit $run_rc); see $OUT/run_1.log" 1
"$HA_PYTHON" "$HA_ROOT/scripts/local_qwen/verify_clawteam_run.py" "$result" --out "$OUT/integration_verdict.json"
