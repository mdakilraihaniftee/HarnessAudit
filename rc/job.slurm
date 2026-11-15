#!/bin/bash -l
# rc/job.slurm — generic job body for every project and both clusters.
# Submit it ONLY through rc/submit.sh, which sets account, partition, GPU,
# CPUs, memory, time and log paths. Nothing project-specific lives here.
set -euo pipefail
umask 0007
cd "$SLURM_SUBMIT_DIR"
source rc/common.sh
MODE="${1:?mode missing — submit with rc/submit.sh}"

rc_activate

if [[ "$NEEDS_SECRETS" == 1 ]]; then
  SECRETS="$HOME/.config/rit-secrets/${REPO}.env"
  [[ -f "$SECRETS" ]] || rc_die "NEEDS_SECRETS=1 but $SECRETS does not exist"
  set -a
  # shellcheck source=/dev/null
  source "$SECRETS"
  set +a
fi

mkdir -p "$RUNDIR"
MARKER="$RUNDIR/${CLUSTER}_${SLURM_JOB_ID}"
touch "$MARKER"
trap 'rm -f "$MARKER"' EXIT

DIRTY=""; [[ -n "$(git status --porcelain --untracked-files=no)" ]] && DIRTY=" (+ local edits!)"
echo "================ rc job ================"
echo "job     : $SLURM_JOB_ID  ${SLURM_JOB_NAME:-}"
echo "node    : $(hostname)  [$CLUSTER / $(uname -m)]"
echo "commit  : $(git rev-parse --short HEAD)$DIRTY"
echo "env     : $VENV"
echo "python  : $(python --version 2>&1)"
echo "start   : $(date -Is)"
if [[ -n "${CUDA_VISIBLE_DEVICES:-}" ]] && command -v nvidia-smi >/dev/null; then
  nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader 2>&1 | sed 's/^/gpu     : /'
else
  echo "gpu     : none (CPU job)"
fi
echo "========================================"

if [[ "$MODE" == check ]]; then
  CHECK_IMPORTS="$CHECK_IMPORTS" python - <<'PY'
import importlib, os, platform, sys
ok = True
print(f"python {sys.version.split()[0]} | {platform.machine()}")
want_gpu = bool(os.environ.get("CUDA_VISIBLE_DEVICES"))
try:
    import torch
except ImportError:
    torch = None
    print("torch : not installed")
if torch is not None:
    archs = torch.cuda.get_arch_list()
    print(f"torch {torch.__version__} | built for CUDA {torch.version.cuda} | kernels {archs}")
    if torch.cuda.is_available():
        dev = torch.cuda.current_device()
        cap = "sm_%d%d" % torch.cuda.get_device_capability(dev)
        print(f"GPU   : {torch.cuda.get_device_name(dev)} ({cap})")
        if archs and cap not in archs:
            print(f"WARNING: this torch build has no native kernels for {cap}")
        x = torch.randn(2048, 2048, device="cuda")
        (x @ x).sum().item()
        torch.cuda.synchronize()
        print("CUDA matmul: OK")
    elif want_gpu:
        ok = False
        print("ERROR: a GPU was allocated but torch.cuda.is_available() is False.\n"
              "       Usually: CPU-only torch wheel, or the wheel's CUDA is newer than the driver above.")
for name in os.environ.get("CHECK_IMPORTS", "").split():
    try:
        importlib.import_module(name)
        print(f"import {name}: OK")
    except Exception as e:
        ok = False
        print(f"import {name}: FAILED -> {type(e).__name__}: {e}")
print("CHECK PASSED" if ok else "CHECK FAILED")
sys.exit(0 if ok else 1)
PY
  exit 0
fi

case "$MODE" in
  prep)  CMD="$PREP_CMD" ;;
  smoke) CMD="$SMOKE_CMD" ;;
  full)  CMD="$FULL_CMD" ;;
  *)     CMD="$MODE" ;;
esac

echo "+ $CMD"
set +e
bash -c "$CMD"
rc=$?
set -e
echo "================ finished $(date -Is)  exit=$rc ================"
exit "$rc"
