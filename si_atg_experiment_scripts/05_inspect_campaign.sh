#!/usr/bin/env bash
set -Eeuo pipefail
source "$(dirname "$0")/common.sh"
verify_repo
CAMPAIGN_ID="${CAMPAIGN_ID:-}"
if [[ -z "$CAMPAIGN_ID" ]]; then
  echo 'Set CAMPAIGN_ID to an ID printed by your campaign command.' >&2
  echo 'For example: CAMPAIGN_ID=... MEMORY_DB=... bash 05_inspect_campaign.sh' >&2
  exit 2
fi

for subcommand in memory best report; do
  EXTRA_FLAGS=()
  if [[ -n "${MEMORY_DB:-}" ]]; then
    add_if_supported "$subcommand" --memory-db "$MEMORY_DB"
  fi
  run_logged "${CAMPAIGN_ID}_${subcommand}" \
    python -m multi_agent.attacks "$subcommand" \
    --campaign-id "$CAMPAIGN_ID" "${EXTRA_FLAGS[@]}"
done

echo 'Compare submitted candidates, violations by type, completion, and harness promotion/rejection.'
