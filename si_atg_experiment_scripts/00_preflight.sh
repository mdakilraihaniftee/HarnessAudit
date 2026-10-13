#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"
verify_repo

echo '=== Python / Codex checks ==='
python --version
if command -v codex >/dev/null 2>&1; then codex --version; else echo 'WARNING: codex not on PATH (mock tests can still work).'; fi

echo '=== Attacker CLI commands ==='
python -m multi_agent.attacks --help

echo '=== Finance scenario inspection (no LLM execution) ==='
run_logged preflight_inspect python -m multi_agent.attacks inspect --task "$TASK"

if [[ -d tests/attacks ]] && python -m pytest --version >/dev/null 2>&1; then
  echo '=== Optional local attacker tests ==='
  run_logged preflight_pytest python -m pytest -q tests/attacks
else
  echo 'NOTE: tests/attacks or pytest not found; skipping optional test suite.'
fi
