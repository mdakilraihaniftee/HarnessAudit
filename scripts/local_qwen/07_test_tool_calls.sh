#!/usr/bin/env bash
# 07_test_tool_calls.sh — real OpenAI-style tool calling against the local model:
# structured tool_calls, schema-validated arguments, tool result consumed in turn 2.
# Usage: scripts/local_qwen/07_test_tool_calls.sh
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
ha_require_python
OUT="$(ha_new_run_dir test_tools)"
args=(test-tools --out "$OUT/test_tools.json")
[[ -n "${ENDPOINT:-}" ]] && args+=(--endpoint "$ENDPOINT")
ha_run_logged "$OUT/test_tools.log" ha_li "${args[@]}"
