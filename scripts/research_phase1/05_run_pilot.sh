#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
phase1_run pilot pilot "${REPEATS:-3}" "${MAX_TARGET_RUNS:-9}"
