#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"
verify_repo

# Does not launch ClawTeam/Codex victim agents.
run_logged mock_dry_run python -m multi_agent.attacks campaign \
  --task "$TASK" --backend mock --rounds 3 --dry-run
run_logged mock_simulation python -m multi_agent.attacks simulate \
  --task "$TASK" --rounds 3

echo 'CHECK: Did rounds 2/3 use the previous feedback? Was memory persisted?'
echo 'Mock results prove pipeline integration, not target vulnerability.'
