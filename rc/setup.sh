#!/usr/bin/env bash
# rc/setup.sh — build or refresh this repo's Python env for the cluster you are
# logged into (SPORC or TIGRIS). Installs exactly what uv.lock says. Safe to re-run.
set -euo pipefail
umask 0007
source "$(dirname "$0")/common.sh"
cd "$PROJECT_ROOT"

[[ -f pyproject.toml && -f uv.lock ]] \
  || rc_die "pyproject.toml and uv.lock must be committed in the repo (PI agent: see rc/AGENT_TASK.md)"

rc_ensure_uv
rc_info "uv $("$UV" --version | awk '{print $2}') | cluster=$CLUSTER | env=$VENV"

mkdir -p data logs results checkpoints "$RUNDIR" "$(dirname "$VENV")" \
         "$UV_CACHE_DIR" "$HF_HUB_CACHE" "$HF_DATASETS_CACHE" "$TORCH_HOME"
chmod 2770 data logs results checkpoints 2>/dev/null || true
if [[ -d .git/info ]]; then
  for d in data/ cache/ logs/ results/ checkpoints/; do
    grep -qxF "$d" .git/info/exclude 2>/dev/null || echo "$d" >> .git/info/exclude
  done
fi

if ! "$UV" sync --locked; then
  rc_die "uv sync failed (error above). Don't fix it here: paste the error to the PI coding agent.
    'lockfile needs to be updated'  -> agent runs 'uv lock' on PI and pushes
    'no wheel' / build failed        -> a dependency doesn't support $(uname -m); agent pins/replaces it"
fi
extra_setup || rc_die "extra_setup() in rc/project.env failed (error above) — paste it to the PI agent"
rc_lock_sha > "$VENV/.rc_lock_sha"

PYTHONNOUSERSITE=1 "$VENV/bin/python" - <<'PY'
import platform, sys
print(f"rc: python {sys.version.split()[0]} on {platform.machine()}")
try:
    import torch
    print(f"rc: torch {torch.__version__} (CUDA build {torch.version.cuda}); "
          f"GPU visible here: {torch.cuda.is_available()}  (False is normal on a login node)")
except ImportError:
    print("rc: torch not installed (fine if this project doesn't use it)")
PY
rc_info "env ready. Next: rc/submit.sh check"
