#!/usr/bin/env bash
# 06_test_inference.sh — health of the local endpoint: model listing, chat,
# structured JSON output, Qwen3 reasoning parser, concurrent requests, timeouts.
# Usage: scripts/local_qwen/06_test_inference.sh
#        ENDPOINT=http://127.0.0.1:8001/v1 scripts/local_qwen/06_test_inference.sh
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
ha_require_python
OUT="$(ha_new_run_dir test_inference)"
args=(health --checks "${CHECKS:-chat,structured,reasoning,concurrency,timeout}" --out "$OUT/health.json")
[[ -n "${ENDPOINT:-}" ]] && args+=(--endpoint "$ENDPOINT")
ha_run_logged "$OUT/health.log" ha_li "${args[@]}"
