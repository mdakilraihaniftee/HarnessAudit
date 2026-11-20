#!/usr/bin/env bash
# test_local_stack.sh — everything that can be verified on this machine, in order:
# unit tests, cost guard, GPU selection, resume, then (only if a local server is
# healthy) live health + tool calling. Live checks are reported SKIPPED, not PASS,
# when no server is running.
# Usage: scripts/tests/test_local_stack.sh
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../local_qwen/lib.sh"
ha_require_python
declare -A R
run() { local name="$1"; shift; if "$@" > "/tmp/ha_stack_$name.log" 2>&1; then R[$name]=PASS; else R[$name]=FAIL; fi; }
run pytest "$HA_PYTHON" -m pytest -q "$HA_ROOT/tests"
run cost_guard "$HA_ROOT/scripts/tests/test_cost_guard.sh"
run gpu_selection "$HA_ROOT/scripts/tests/test_gpu_selection.sh"
run resume_campaign "$HA_ROOT/scripts/tests/test_resume_campaign.sh"
if ha_li health --checks chat >/dev/null 2>&1; then
  run live_health ha_li health
  run live_tool_calls ha_li test-tools
  run live_verify_cost ha_li verify-cost --attacker-backend qwen_code
else
  R[live_health]=SKIPPED; R[live_tool_calls]=SKIPPED; R[live_verify_cost]=SKIPPED
fi
fail=0
for k in pytest cost_guard gpu_selection resume_campaign live_health live_tool_calls live_verify_cost; do
  printf '%-8s %s  (log /tmp/ha_stack_%s.log)\n' "${R[$k]}" "$k" "$k"
  [[ "${R[$k]}" == FAIL ]] && fail=1
done
exit $fail
