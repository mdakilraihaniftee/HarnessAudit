#!/usr/bin/env bash
# rc/preflight.sh [--smoke] — run on the PI cluster (by you or the coding agent)
# BEFORE pushing. It catches, in seconds, the problems that would otherwise
# only show up after a queue wait on RIT. Exit code 0 = safe to push.
#   --smoke   also run SMOKE_CMD locally through the locked env (uv run --locked)
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
UV="${UV:-uv}"
fail=0
ok()   { echo "  ok    $*"; }
bad()  { echo "  FAIL  $*"; fail=1; }
warn() { echo "  warn  $*"; }

echo "[1] dependencies are locked"
if [[ -f pyproject.toml && -f uv.lock ]]; then ok "pyproject.toml + uv.lock present"
else bad "pyproject.toml / uv.lock missing (see rc/AGENT_TASK.md)"; fi
if "$UV" lock --locked >/dev/null 2>&1; then ok "uv.lock matches pyproject.toml"
else bad "uv.lock is stale -> run: uv lock"; fi
grep -q 'required-environments' pyproject.toml 2>/dev/null \
  && ok "required-environments declared" \
  || bad "add [tool.uv] required-environments for linux x86_64 + aarch64 (rc/AGENT_TASK.md)"
grep -qE 'download\.pytorch\.org/whl/cu[0-9]+' pyproject.toml 2>/dev/null \
  && ok "torch pinned to a CUDA index: $(grep -oE 'whl/cu[0-9]+' pyproject.toml | head -1)" \
  || warn "torch not pinned to a CUDA-specific index (fine only if the project has no torch)"
[[ -f .python-version ]] && ok ".python-version = $(cat .python-version)" || warn "no .python-version"

echo "[2] every locked package has a wheel for both RIT architectures (best effort)"
REQ=$(mktemp)
if "$UV" export --frozen --no-hashes --no-emit-project --no-header -o "$REQ" >/dev/null 2>&1; then
  PYV=$(cat .python-version 2>/dev/null || echo 3.12)
  IDX=$(grep -oE 'https://download\.pytorch\.org/whl/cu[0-9]+' pyproject.toml | head -1)
  EXTRA=(); [[ -n "$IDX" ]] && EXTRA=(--extra-index-url "$IDX" --index-strategy unsafe-best-match)
  for plat in x86_64-manylinux_2_28 aarch64-manylinux_2_28; do
    if out=$("$UV" pip compile "$REQ" --python-platform "$plat" --python-version "$PYV" \
              --only-binary :all: "${EXTRA[@]}" -q -o /dev/null 2>&1); then
      ok "$plat: all wheels available"
    else
      warn "$plat: some package has no prebuilt wheel. Harmless if it is pure Python;"
      warn "        if it has C/CUDA code it will probably fail to build on RIT:"
      echo "$out" | tail -n 6 | sed 's/^/          /'
    fi
  done
else
  warn "could not export uv.lock (skipped)"
fi
rm -f "$REQ"

echo "[3] code is portable"
hits=$(git grep -nE '(/home/|/mnt/|/scratch/|/data/|/Users/|/shared/)[A-Za-z0-9_.-]+' \
        -- '*.py' '*.sh' '*.yaml' '*.yml' '*.json' '*.cfg' ':!rc/' 2>/dev/null | head -15)
[[ -z "$hits" ]] && ok "no hard-coded absolute paths" \
  || { warn "hard-coded absolute paths (turn into CLI args / env vars):"; echo "$hits" | sed 's/^/          /'; }
hits=$(git grep -nE "cuda:[1-9]|CUDA_VISIBLE_DEVICES\"?\]? *=|pip install|os\.system\(.*pip" -- '*.py' ':!rc/' 2>/dev/null | head -10)
[[ -z "$hits" ]] && ok "no hard-coded GPU index / runtime pip installs" \
  || { warn "check these lines:"; echo "$hits" | sed 's/^/          /'; }

echo "[4] rc contract"
if [[ -f rc/project.env ]]; then
  # shellcheck source=/dev/null
  ( source rc/project.env
    [[ -n "${SMOKE_CMD:-}" ]] && echo "  ok    SMOKE_CMD = $SMOKE_CMD" || { echo "  FAIL  SMOKE_CMD empty"; exit 1; }
    [[ -n "${FULL_CMD:-}"  ]] && echo "  ok    FULL_CMD  = $FULL_CMD"  || { echo "  FAIL  FULL_CMD empty"; exit 1; }
  ) || fail=1
else bad "rc/project.env missing"; fi
for f in rc/setup.sh rc/update.sh rc/submit.sh rc/status.sh rc/preflight.sh rc/job.slurm; do
  mode=$(git ls-files -s "$f" 2>/dev/null | cut -d' ' -f1)
  [[ "$mode" == 100755 ]] || bad "$f not committed as executable -> chmod +x $f && git add $f"
done
bash -n rc/job.slurm && ok "rc/job.slurm syntax OK" || bad "rc/job.slurm syntax error"

if [[ "${1:-}" == --smoke ]]; then
  echo "[5] smoke test on PI through the locked env"
  ( source rc/project.env; "$UV" run --locked bash -c "$SMOKE_CMD" ) \
    && ok "SMOKE_CMD passed" || bad "SMOKE_CMD failed"
fi

echo "[6] git"
[[ -z "$(git status --porcelain --untracked-files=no)" ]] && ok "no uncommitted changes" \
  || bad "uncommitted changes -> commit them"
git fetch -q 2>/dev/null
if [[ "$(git rev-parse HEAD)" == "$(git rev-parse '@{u}' 2>/dev/null)" ]]; then ok "pushed"
else warn "HEAD differs from upstream -> git push"; fi

echo
((fail)) && { echo "PREFLIGHT FAILED"; exit 1; } || echo "PREFLIGHT PASSED — push, then on RIT: rc/update.sh && rc/submit.sh smoke"
