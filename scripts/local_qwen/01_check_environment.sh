#!/usr/bin/env bash
# 01_check_environment.sh — check every dependency of the local stack (no downloads).
# Usage: scripts/local_qwen/01_check_environment.sh
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
fail=0
ok()  { printf '  ok    %s\n' "$*"; }
bad() { printf '  FAIL  %s\n' "$*"; fail=1; }
warn(){ printf '  warn  %s\n' "$*"; }
echo "host $(hostname)  arch $HA_ARCH  repo $HA_ROOT"
[[ -x "$HA_PYTHON" ]] && ok "HarnessAudit python: $HA_PYTHON ($("$HA_PYTHON" --version 2>&1))" || bad "no HarnessAudit python (set HA_PYTHON)"
"$HA_PYTHON" -c 'import multi_agent.local_inference, clawteam, mcp, openai' 2>/dev/null \
  && ok "imports: multi_agent.local_inference clawteam mcp openai" || bad "HarnessAudit imports failed"
if [[ -x "$HA_VLLM_VENV/bin/vllm" ]]; then
  v=$("$HA_VLLM_VENV/bin/python" -c 'import vllm,torch;print(vllm.__version__, "torch", torch.__version__, "cuda", torch.version.cuda)' 2>/dev/null)
  ok "vLLM: $v ($HA_VLLM_VENV)"
else bad "vLLM venv missing: $HA_VLLM_VENV (run 03_setup_qwen.sh --allow-download)"; fi
if command -v node >/dev/null; then
  nm=$(node -p 'process.versions.node.split(".")[0]'); (( nm >= 20 )) && ok "node $(node --version)" || bad "node $(node --version) < 20"
else bad "node not found"; fi
if [[ -n "${HA_QWEN_BIN:-}" && -x "$HA_QWEN_BIN" ]]; then ok "qwen code $("$HA_QWEN_BIN" --version 2>/dev/null | tail -1) ($HA_QWEN_BIN)"
elif command -v qwen >/dev/null; then ok "qwen code $(qwen --version 2>/dev/null | tail -1) ($(command -v qwen))"
else bad "Qwen Code CLI missing (run 03_setup_qwen.sh --allow-download --only qwen-code)"; fi
if command -v nvidia-smi >/dev/null; then
  ok "driver $(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1), $(nvidia-smi -L | wc -l) GPU(s) visible to NVML"
else warn "nvidia-smi not found (no GPU on this node?)"; fi
[[ -n "${SLURM_JOB_ID:-}" ]] && ok "inside Slurm job $SLURM_JOB_ID (nodes=${SLURM_JOB_NUM_NODES:-?} CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES:-unset})" \
  || ok "not inside a Slurm allocation"
if [[ -x "$HA_VLLM_VENV/bin/python" ]] && "$HA_VLLM_VENV/bin/python" -c "
import sys
from huggingface_hub import snapshot_download
snapshot_download(sys.argv[1], local_files_only=True)" "$HA_MODEL" >/dev/null 2>&1; then
  ok "model $HA_MODEL cached (${HF_HUB_CACHE:-$HOME/.cache/huggingface/hub})"
else bad "model $HA_MODEL not cached (03_setup_qwen.sh --allow-download --only model)"; fi
avail=$(df -Pk "${HF_HUB_CACHE:-$HOME/.cache/huggingface}" 2>/dev/null | awk 'NR==2{print int($4/1048576)}')
[[ -n "$avail" ]] && { (( avail > 20 )) && ok "free disk for HF cache: ${avail} GB" || warn "only ${avail} GB free for HF cache"; }
echo "inference mode: ${HA_INFERENCE_MODE}"
(( fail )) && { echo "ENVIRONMENT CHECK FAILED"; exit 1; }
echo "ENVIRONMENT CHECK PASSED"
