# shellcheck shell=bash
# scripts/local_qwen/lib.sh — sourced by every local-Qwen script. Do not run directly.
#
# Resolves repository paths, the HarnessAudit Python, the (separate) vLLM
# environment and the Qwen Code CLI without hard-coding machine paths. Every
# location can be overridden with an environment variable.

HA_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export HA_ROOT
cd "$HA_ROOT"

HA_ARCH="$(uname -m)"

# HarnessAudit interpreter: explicit > repo .venv > active venv/rc env > python3.
if [[ -z "${HA_PYTHON:-}" ]]; then
  if [[ -x "$HA_ROOT/.venv/bin/python" ]]; then
    HA_PYTHON="$HA_ROOT/.venv/bin/python"
  elif [[ -n "${VIRTUAL_ENV:-}" && -x "$VIRTUAL_ENV/bin/python" ]]; then
    HA_PYTHON="$VIRTUAL_ENV/bin/python"
  else
    HA_PYTHON="$(command -v python3 || command -v python || true)"
  fi
fi
export HA_PYTHON

# vLLM lives in its own environment (it pins torch/CUDA; HarnessAudit does not).
HA_VLLM_VENV="${HA_VLLM_VENV:-$HOME/venvs/HarnessAudit-vllm-$HA_ARCH}"
HA_VLLM_VERSION="${HA_VLLM_VERSION:-0.33.0}"
export HA_VLLM_VENV HA_VLLM_VERSION

# Qwen Code CLI lives in a dedicated npm prefix so it never collides with other CLIs.
HA_QWEN_CODE_VERSION="${HA_QWEN_CODE_VERSION:-0.27.4}"
HA_QWEN_CODE_PREFIX="${HA_QWEN_CODE_PREFIX:-$HOME/.local/share/harnessaudit/qwen-code-$HA_ARCH}"
if [[ -z "${HA_QWEN_BIN:-}" && -x "$HA_QWEN_CODE_PREFIX/bin/qwen" ]]; then
  HA_QWEN_BIN="$HA_QWEN_CODE_PREFIX/bin/qwen"
fi
export HA_QWEN_CODE_VERSION HA_QWEN_CODE_PREFIX HA_QWEN_BIN

HA_MODEL="${HA_MODEL:-Qwen/Qwen3-8B}"
HA_CONFIG="${HA_CONFIG:-$HA_ROOT/configs/local_qwen.yaml}"
HA_RUN_ROOT="${HA_RUN_ROOT:-$HA_ROOT/results/local_qwen}"
export HA_MODEL HA_CONFIG HA_RUN_ROOT

# Fully local is the default. Paid providers need HA_INFERENCE_MODE=paid_opt_in.
export HA_INFERENCE_MODE="${HA_INFERENCE_MODE:-local_only}"

ha_info() { echo "[local_qwen] $*" >&2; }
ha_die()  { echo "[local_qwen] ERROR: $*" >&2; exit "${2:-1}"; }

ha_require_cmd() {
  local cmd
  for cmd in "$@"; do
    command -v "$cmd" >/dev/null 2>&1 || ha_die "required command not found: $cmd"
  done
}

ha_require_python() {
  [[ -n "$HA_PYTHON" && -x "$HA_PYTHON" ]] || ha_die "no HarnessAudit python (set HA_PYTHON)"
  "$HA_PYTHON" -c 'import multi_agent.local_inference' 2>/dev/null \
    || ha_die "cannot import multi_agent.local_inference with $HA_PYTHON (set HA_PYTHON)"
}

ha_li() { "$HA_PYTHON" -m multi_agent.local_inference "$@"; }

# Unique, timestamped output directory under HA_RUN_ROOT/<label>.
ha_new_run_dir() {
  local label="$1" dir
  dir="$HA_RUN_ROOT/${label}_$(date +%Y%m%dT%H%M%S)_$$"
  mkdir -p "$dir"
  echo "$dir"
}

# Run a command, tee its output to a log file, and return the command's exit code.
ha_run_logged() {
  local log="$1"; shift
  ha_info "log: $log"
  printf '+' >> "$log"; printf ' %q' "$@" >> "$log"; printf '\n' >> "$log"
  set +e
  "$@" 2>&1 | tee -a "$log"
  local rc=${PIPESTATUS[0]}
  set -e
  return "$rc"
}

# Read one value from the effective config (dotted key), e.g. ha_cfg inference.endpoint
ha_cfg() { ha_li config --get "$1" 2>/dev/null; }

if [[ -n "${HA_QWEN_BIN:-}" ]]; then
  PATH="$(dirname "$HA_QWEN_BIN"):$PATH"
  export PATH
fi
