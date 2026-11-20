#!/usr/bin/env bash
# 00_check_costs.sh — prove the current configuration cannot incur model API charges.
#
# Runs the full cost audit and the strict verify-cost gate. Exit codes:
#   0 local-only and every route resolvable   2 a route is billable/unapproved
#   3 a route is unresolved (e.g. server down; use STATIC=1 to skip probing)
#   4 paid_opt_in is active
# Usage:
#   scripts/local_qwen/00_check_costs.sh
#   STATIC=1 scripts/local_qwen/00_check_costs.sh          # before the server is up
#   ATTACKER_BACKEND=qwen_code scripts/local_qwen/00_check_costs.sh
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
ha_require_python
OUT="$(ha_new_run_dir cost_audit)"
args=()
[[ "${STATIC:-0}" == 1 ]] && args+=(--static)
[[ -n "${ATTACKER_BACKEND:-qwen_code}" ]] && args+=(--attacker-backend "${ATTACKER_BACKEND:-qwen_code}")
ha_li audit "${args[@]}" --out "$OUT/audit.json" > "$OUT/audit.txt" || true
set +e
ha_li verify-cost "${args[@]}" --out "$OUT/verify_cost.json" | tee "$OUT/verify_cost.txt"
rc=${PIPESTATUS[0]}
set -e
ha_info "full audit: $OUT/audit.txt ; verdict JSON: $OUT/verify_cost.json"
exit "$rc"
