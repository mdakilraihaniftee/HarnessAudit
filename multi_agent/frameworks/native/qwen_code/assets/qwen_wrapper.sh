#!/usr/bin/env bash
# HarnessAudit qwen_code harness wrapper (copied into each run's isolation dir).
# ClawTeam spawns this file as "qwen" and appends `--yolo -p <prompt>`. Per-agent
# settings arrive through HA_QWEN_* variables set by QwenCodeHarnessRuntime.
set -o pipefail
: "${HA_QWEN_REAL_BIN:?}" "${HA_QWEN_TRACE_FILE:?}" "${HA_QWEN_BASE_URL:?}" "${HA_QWEN_MODEL:?}"
extra=(--openai-base-url "$HA_QWEN_BASE_URL" --model "$HA_QWEN_MODEL")
if [[ -n "${HA_QWEN_MCP_CONFIG:-}" ]]; then
  extra+=(--mcp-config "$HA_QWEN_MCP_CONFIG")
fi
if [[ -s "${HA_QWEN_SYSTEM_PROMPT_FILE:-}" ]]; then
  extra+=(--append-system-prompt "$(cat "$HA_QWEN_SYSTEM_PROMPT_FILE")")
fi
"$HA_QWEN_REAL_BIN" "${extra[@]}" "$@" < /dev/null 2>> "${HA_QWEN_STDERR_FILE:-/dev/null}" \
  | "${HA_QWEN_NODE:-node}" "$HA_QWEN_STAMPER" "$HA_QWEN_TRACE_FILE"
