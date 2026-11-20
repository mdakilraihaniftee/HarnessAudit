#!/usr/bin/env bash
# 03_setup_qwen.sh — install the local inference stack (idempotent).
#
#   vLLM         into a dedicated venv ($HA_VLLM_VENV), pinned to $HA_VLLM_VERSION
#   Qwen Code    into a dedicated npm prefix ($HA_QWEN_CODE_PREFIX), pinned to $HA_QWEN_CODE_VERSION
#   model        $HA_MODEL weights into the Hugging Face cache (HF_HUB_CACHE)
#
# Nothing is downloaded unless you pass --allow-download (or HA_ALLOW_DOWNLOAD=1).
# Without it the script only reports what is installed and what is missing.
#
# Usage:
#   scripts/local_qwen/03_setup_qwen.sh                       # check only
#   scripts/local_qwen/03_setup_qwen.sh --allow-download      # install everything missing
#   scripts/local_qwen/03_setup_qwen.sh --allow-download --only model
#   HA_MODEL=Qwen/Qwen3-14B scripts/local_qwen/03_setup_qwen.sh --allow-download --only model
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

ALLOW_DOWNLOAD="${HA_ALLOW_DOWNLOAD:-0}"
ONLY="all"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --allow-download) ALLOW_DOWNLOAD=1 ;;
    --only) ONLY="${2:?--only needs vllm|qwen-code|model|ray}"; shift ;;
    -h|--help) sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) ha_die "unknown argument: $1" 2 ;;
  esac
  shift
done
want() { [[ "$ONLY" == all || "$ONLY" == "$1" ]]; }
missing=0

# ---------------------------------------------------------------- vLLM
if want vllm; then
  if [[ -x "$HA_VLLM_VENV/bin/vllm" ]] \
     && "$HA_VLLM_VENV/bin/python" -c "import vllm,sys; sys.exit(vllm.__version__ != '$HA_VLLM_VERSION')" 2>/dev/null; then
    ha_info "vLLM $HA_VLLM_VERSION present in $HA_VLLM_VENV"
  elif [[ "$ALLOW_DOWNLOAD" == 1 ]]; then
    ha_require_cmd uv
    ha_info "installing vLLM $HA_VLLM_VERSION into $HA_VLLM_VENV ($HA_ARCH)"
    mkdir -p "$(dirname "$HA_VLLM_VENV")"
    [[ -x "$HA_VLLM_VENV/bin/python" ]] || uv venv --python "${HA_VLLM_PYTHON:-3.12}" "$HA_VLLM_VENV"
    VIRTUAL_ENV="$HA_VLLM_VENV" uv pip install --python "$HA_VLLM_VENV/bin/python" \
      "vllm==$HA_VLLM_VERSION"
    "$HA_VLLM_VENV/bin/python" -c 'import vllm, torch; print("vllm", vllm.__version__, "torch", torch.__version__, "cuda", torch.version.cuda)'
  else
    ha_info "MISSING: vLLM $HA_VLLM_VERSION in $HA_VLLM_VENV (re-run with --allow-download)"
    missing=1
  fi
  # Precompiled FlashInfer kernels: without them vLLM JIT-compiles at startup and
  # needs a CUDA toolkit matching its torch build (often absent on clusters).
  if [[ -x "$HA_VLLM_VENV/bin/vllm" ]]; then
    if "$HA_VLLM_VENV/bin/python" -c 'import importlib.util,sys; sys.exit(not any(importlib.util.find_spec(m) for m in ("flashinfer_cubin","flashinfer_jit_cache")))' 2>/dev/null; then
      ha_info "FlashInfer precompiled kernels present"
    elif [[ "$ALLOW_DOWNLOAD" == 1 ]]; then
      ha_info "installing precompiled vLLM/FlashInfer kernels (vllm download-kernels)"
      PATH="$HA_VLLM_VENV/bin:$PATH" VIRTUAL_ENV="$HA_VLLM_VENV" "$HA_VLLM_VENV/bin/vllm" download-kernels
    else
      ha_info "MISSING: precompiled kernels (re-run with --allow-download)"
      missing=1
    fi
  fi
fi

# ---------------------------------------------------------------- Qwen Code
if want qwen-code; then
  qbin="$HA_QWEN_CODE_PREFIX/bin/qwen"
  if [[ -x "$qbin" ]] && [[ "$("$qbin" --version 2>/dev/null)" == *"$HA_QWEN_CODE_VERSION"* ]]; then
    ha_info "Qwen Code $HA_QWEN_CODE_VERSION present at $qbin"
  elif [[ "$ALLOW_DOWNLOAD" == 1 ]]; then
    ha_require_cmd node npm
    node_major="$(node -p 'process.versions.node.split(".")[0]')"
    (( node_major >= 20 )) || ha_die "Qwen Code needs Node.js >= 20 (found $(node --version))"
    ha_info "installing @qwen-code/qwen-code@$HA_QWEN_CODE_VERSION into $HA_QWEN_CODE_PREFIX"
    mkdir -p "$HA_QWEN_CODE_PREFIX"
    npm install -g --prefix "$HA_QWEN_CODE_PREFIX" --no-fund --no-audit \
      "@qwen-code/qwen-code@$HA_QWEN_CODE_VERSION"
    "$qbin" --version
  else
    ha_info "MISSING: Qwen Code $HA_QWEN_CODE_VERSION at $qbin (re-run with --allow-download)"
    missing=1
  fi
fi

# ---------------------------------------------------------------- model weights
if want model; then
  [[ -x "$HA_VLLM_VENV/bin/python" ]] || ha_die "install vLLM first (it provides huggingface_hub)"
  if "$HA_VLLM_VENV/bin/python" - "$HA_MODEL" <<'PY'
import sys
from huggingface_hub import snapshot_download
try:
    path = snapshot_download(sys.argv[1], local_files_only=True,
                             allow_patterns=["*.json", "*.safetensors", "*.txt", "*.model"])
except Exception:
    sys.exit(1)
import pathlib
ok = any(pathlib.Path(path).glob("*.safetensors"))
print(path if ok else "", file=sys.stderr)
sys.exit(0 if ok else 1)
PY
  then
    ha_info "model $HA_MODEL already cached (${HF_HUB_CACHE:-$HOME/.cache/huggingface/hub})"
  elif [[ "$ALLOW_DOWNLOAD" == 1 ]]; then
    ha_info "downloading $HA_MODEL into ${HF_HUB_CACHE:-$HOME/.cache/huggingface/hub}"
    "$HA_VLLM_VENV/bin/python" - "$HA_MODEL" "${HA_MODEL_REVISION:-}" <<'PY'
import sys
from huggingface_hub import snapshot_download
path = snapshot_download(sys.argv[1], revision=sys.argv[2] or None,
                         allow_patterns=["*.json", "*.safetensors", "*.txt", "*.model",
                                         "LICENSE*", "README*", "*.jinja"])
print("downloaded to", path)
PY
  else
    ha_info "MISSING: model $HA_MODEL not cached (re-run with --allow-download)"
    missing=1
  fi
fi

# ---------------------------------------------------------------- Ray (multi-node only)
# Only needed for one model spanning several nodes (scripts/hpc/slurm_qwen_multinode.sbatch).
# Never installed by default: request it explicitly with --only ray.
if [[ "$ONLY" == ray ]]; then
  if "$HA_VLLM_VENV/bin/python" -c 'import ray' 2>/dev/null; then
    ha_info "ray present in $HA_VLLM_VENV"
  elif [[ "$ALLOW_DOWNLOAD" == 1 ]]; then
    ha_require_cmd uv
    uv pip install --python "$HA_VLLM_VENV/bin/python" "ray[default]==${HA_RAY_VERSION:-2.59.0}"
  else
    ha_info "MISSING: ray in $HA_VLLM_VENV (re-run with --allow-download --only ray)"
    missing=1
  fi
fi

if (( missing )); then
  ha_info "setup incomplete (see MISSING lines above)"
  exit 3
fi
ha_info "setup complete"
