# shellcheck shell=bash
# shellcheck disable=SC2034   # variables are used by the scripts that source this file
# rc/common.sh — sourced by the other rc scripts. Do not run or source it by hand.
# Works out which cluster you are on and where every file lives, so no
# script ever needs a hand-edited path.

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO="$(basename "$PROJECT_ROOT")"
BASE="$(dirname "$PROJECT_ROOT")"          # e.g. /shared/rc/defake/agentic_security
RUNDIR="$PROJECT_ROOT/logs/.running"       # job markers, visible from both clusters

rc_die()  { echo "rc: ERROR: $*" >&2; exit 1; }
rc_info() { echo "rc: $*" >&2; }

# ---------- defaults (override any of these in rc/project.env) ----------
RC_ACCOUNT=defake
PREP_CMD=""; SMOKE_CMD=""; FULL_CMD=""
CHECK_IMPORTS=""
RESULT_GLOB="results/*"
CPUS=4; MEM=32G; GPUS=1
TIME_CHECK=00:15:00; TIME_PREP=02:00:00; TIME_SMOKE=00:30:00; TIME_FULL=1-00:00:00
NEEDS_SECRETS=0
PARTITION_SPORC=sporc-gpu; CPU_PARTITION_SPORC=sporc
PARTITION_TIGRIS=tigris;   CPU_PARTITION_TIGRIS=tigris
cluster_env() { :; }
extra_setup() { :; }    # non-Python tools (Node CLIs etc.), run by rc/setup.sh

if [[ -f "$PROJECT_ROOT/rc/project.env" ]]; then
  # shellcheck source=/dev/null
  source "$PROJECT_ROOT/rc/project.env"
fi

# ---------- which cluster? (login and compute nodes share an arch) ----------
case "${RC_CLUSTER:-$(uname -m)}" in
  sporc|x86_64)
    CLUSTER=sporc;  PARTITION=$PARTITION_SPORC;  CPU_PARTITION=$CPU_PARTITION_SPORC
    GPU_TYPE=a100;  UV_DEFAULT="$HOME/.local/bin/uv" ;;
  tigris|aarch64)
    CLUSTER=tigris; PARTITION=$PARTITION_TIGRIS; CPU_PARTITION=$CPU_PARTITION_TIGRIS
    GPU_TYPE=gh200; UV_DEFAULT="$HOME/.local/uv-tigris/uv" ;;
  *) rc_die "unknown cluster/arch '${RC_CLUSTER:-$(uname -m)}' (set RC_CLUSTER=sporc|tigris)" ;;
esac
[[ -d /shared/rc ]] || rc_info "WARNING: /shared/rc not found — these scripts are meant for RIT SPORC/TIGRIS"

# ---------- locations ----------
# $HOME is shared by both clusters, so the uv binary and each venv are per-architecture.
UV="${RC_UV:-$UV_DEFAULT}"
VENV="${RC_ENV_ROOT:-$HOME/venvs}/${REPO}-${CLUSTER}"
HF_ROOT="${RC_HF_CACHE:-$BASE/.cache/huggingface}"   # shared by all projects: download a model once

export UV_PROJECT_ENVIRONMENT="$VENV"                # uv sync installs into this venv, not ./.venv
export UV_CACHE_DIR="${RC_UV_CACHE:-$BASE/.uv-cache/$CLUSTER}"
export UV_LINK_MODE=copy                             # cache and venv are on different filesystems
export UV_PYTHON_PREFERENCE=only-managed             # never pick up system/Spack python by accident
export HF_HUB_CACHE="$HF_ROOT/hub"                   # (HF_HOME stays default, so your token stays private)
export HF_DATASETS_CACHE="$HF_ROOT/datasets"
export HF_XET_CACHE="$HF_ROOT/xet"
export TORCH_HOME="$BASE/.cache/torch"
export TOKENIZERS_PARALLELISM=false
export PYTHONNOUSERSITE=1                            # ignore ~/.local/lib/python* (wrong arch half the time)
export PROJECT_ROOT REPO CLUSTER

# Env fingerprint: changes when uv.lock OR rc/project.env (tool versions) change.
rc_lock_sha() { cat "$PROJECT_ROOT/uv.lock" "$PROJECT_ROOT/rc/project.env" 2>/dev/null | sha256sum | cut -d' ' -f1; }

# Install an official Node.js build for this cluster's arch inside the venv dir
# (so SPORC and TIGRIS each get their own). Use it from extra_setup() in project.env,
# then 'npm install -g ...' puts CLIs in $VENV/node/bin, which rc_activate puts on PATH.
rc_install_node() {
  local ver="${1:?usage: rc_install_node 22.12.0}" arch dir="$VENV/node"
  case "$(uname -m)" in x86_64) arch=x64 ;; aarch64) arch=arm64 ;; *) rc_die "no node build for $(uname -m)" ;; esac
  if [[ -x "$dir/bin/node" && "$("$dir/bin/node" --version)" == "v$ver" ]]; then
    rc_info "node v$ver already installed"
  else
    rc_info "installing node v$ver ($arch) into $dir"
    rm -rf "$dir" && mkdir -p "$dir"
    curl -fsSL "https://nodejs.org/dist/v$ver/node-v$ver-linux-$arch.tar.xz" | tar -xJ -C "$dir" --strip-components=1
  fi
  export PATH="$dir/bin:$PATH"
}

rc_ensure_uv() {
  if [[ ! -x "$UV" ]]; then
    rc_info "uv not found at $UV — installing it for $CLUSTER"
    curl -LsSf https://astral.sh/uv/install.sh \
      | env UV_INSTALL_DIR="$(dirname "$UV")" UV_NO_MODIFY_PATH=1 sh
  fi
}

# Put the env on PATH, refusing to run if it no longer matches uv.lock.
rc_activate() {
  [[ -x "$VENV/bin/python" ]] || rc_die "no env at $VENV — run rc/setup.sh on $CLUSTER first"
  if [[ "$(cat "$VENV/.rc_lock_sha" 2>/dev/null)" != "$(rc_lock_sha)" ]]; then
    rc_die "env is older than uv.lock/project.env — run rc/update.sh (or rc/setup.sh) on $CLUSTER"
  fi
  export VIRTUAL_ENV="$VENV"
  export PATH="$VENV/bin:$PATH"
  [[ -d "$VENV/node/bin" ]] && export PATH="$VENV/node/bin:$PATH"
  unset PYTHONHOME
  cluster_env
}

# Refuse to change code while a job of this repo is running on EITHER cluster.
rc_running_jobs_guard() {
  [[ "${FORCE:-0}" == 1 ]] && return 0
  local m name c j busy=()
  shopt -s nullglob
  for m in "$RUNDIR"/*; do
    name=$(basename "$m"); c=${name%%_*}; j=${name#*_}
    if [[ "$c" == "$CLUSTER" ]] && ! squeue -h -j "$j" -o %i 2>/dev/null | grep -q .; then
      rm -f "$m"                      # stale marker: that job is gone
    else
      busy+=("$c:$j")
    fi
  done
  shopt -u nullglob
  if ((${#busy[@]})); then
    rc_die "job(s) from this repo still running: ${busy[*]}. Wait, or override with FORCE=1."
  fi
}
