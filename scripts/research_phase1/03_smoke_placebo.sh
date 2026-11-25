#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
phase1_run B1 placebo 1 "${MAX_TARGET_RUNS:-1}"
