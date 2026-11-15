#!/usr/bin/env bash
# rc/update.sh — pull the code you pushed from the PI cluster, and rebuild the env
# only if uv.lock changed. Run this every time you want the latest code on RIT.
set -euo pipefail
umask 0007
source "$(dirname "$0")/common.sh"
cd "$PROJECT_ROOT"

rc_running_jobs_guard        # never swap code under a running job (checks both clusters)

before=$(git rev-parse HEAD)
git pull --ff-only || rc_die "git pull failed. Never edit tracked code on RIT — fix it on PI and push.
    See local edits: git status      Drop them: git stash   (or git checkout -- <file>)"
after=$(git rev-parse HEAD)
if [[ "$before" == "$after" ]]; then
  rc_info "already at latest commit $(git rev-parse --short HEAD)"
else
  rc_info "updated $(git rev-parse --short "$before") -> $(git rev-parse --short "$after"):"
  git log --oneline "$before..$after" | sed 's/^/rc:   /'
fi
source "$PROJECT_ROOT/rc/common.sh"   # reload: the pull may have changed project.env or the kit

if [[ -x "$VENV/bin/python" && "$(cat "$VENV/.rc_lock_sha" 2>/dev/null)" == "$(rc_lock_sha)" ]]; then
  rc_info "uv.lock/project.env unchanged — env for $CLUSTER is up to date"
else
  rc_info "uv.lock/project.env changed (or no env yet) — rebuilding env for $CLUSTER"
  bash "$PROJECT_ROOT/rc/setup.sh"
fi
