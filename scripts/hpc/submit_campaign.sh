#!/usr/bin/env bash
# submit_campaign.sh — build and submit a local-Qwen Slurm job from a cluster profile.
#
# Node count, node names, GPUs per node and GPU type are separate options and are
# never derived from each other. Partition/account are taken from the profile
# (configs/cluster/*.yaml) or flags — never invented.
#
# Usage:
#   scripts/hpc/submit_campaign.sh --profile a100_single                       # 1 node campaign
#   scripts/hpc/submit_campaign.sh --profile a100_multinode --nodes 4 \
#        --script slurm_parallel_evaluation.sbatch --mode clean --repeat 8
#   scripts/hpc/submit_campaign.sh --profile a100_multinode --nodes 2 --nodelist gpu03,gpu07
#   scripts/hpc/submit_campaign.sh --profile gh200 --script slurm_gh200_example.sbatch
#   scripts/hpc/submit_campaign.sh --profile a100_single --array 0-3 --rounds 10   # 4 campaigns
#   ... --dry-run    # print the sbatch command only
# Options: --profile P --script S --nodes N --nodelist a,b --exclude c --partition P
#   --account A --time T --cpus-per-task C --mem M --gpus-per-node G --gpu-type T
#   --gres STR --array SPEC --mode campaign|clean --rounds N --arm B6 --backend qwen_code
#   --baseline-runs N --repeat N --judge --model M --dry-run   [-- extra sbatch args]
set -euo pipefail
HA_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$HA_ROOT"
PY="${HA_PYTHON:-$( [[ -x .venv/bin/python ]] && echo .venv/bin/python || command -v python3)}"
PROFILE="" SCRIPT="slurm_finance_campaign.sbatch" DRY=0 EXTRA=() EXPORTS=()
declare -A O=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile) PROFILE="$2"; shift ;;
    --script) SCRIPT="$2"; shift ;;
    --nodes|--nodelist|--exclude|--partition|--account|--time|--cpus-per-task|--mem|--gpus-per-node|--gpu-type|--gres|--array)
      O[${1#--}]="$2"; shift ;;
    --mode) EXPORTS+=("MODE=$2"); shift ;;
    --rounds) EXPORTS+=("ROUNDS=$2"); shift ;;
    --arm) EXPORTS+=("ARM=$2"); shift ;;
    --backend) EXPORTS+=("BACKEND=$2"); shift ;;
    --baseline-runs) EXPORTS+=("BASELINE_RUNS=$2"); shift ;;
    --repeat) EXPORTS+=("REPEAT=$2"); shift ;;
    --model) EXPORTS+=("HA_MODEL=$2"); shift ;;
    --judge) EXPORTS+=("JUDGE=1") ;;
    --dry-run) DRY=1 ;;
    --) shift; EXTRA=("$@"); break ;;
    -h|--help) sed -n '2,26p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
  shift
done
[[ -f "scripts/hpc/$SCRIPT" ]] || { echo "no such script scripts/hpc/$SCRIPT" >&2; exit 2; }
prof() {  # value from the profile's cluster section (empty if unset)
  [[ -n "$PROFILE" ]] || return 0
  local v; v=$("$PY" -m multi_agent.local_inference config --profile "$PROFILE" --get "cluster.$1" 2>/dev/null || true)
  [[ "$v" == None || "$v" == null ]] && v=""
  echo "$v"
}
nodes="${O[nodes]:-}"; nodelist="${O[nodelist]:-}"
if [[ -n "$nodelist" ]]; then
  n_named=$(tr ',' '\n' <<<"$nodelist" | grep -c .)
  [[ -z "$nodes" ]] && nodes="$n_named"
  (( n_named <= nodes )) || { echo "--nodelist names $n_named nodes but --nodes=$nodes" >&2; exit 2; }
  (( n_named == nodes )) || echo "note: Slurm will add $((nodes - n_named)) node(s) beyond --nodelist" >&2
fi
nodes="${nodes:-$(prof nodes)}"; nodes="${nodes:-1}"
[[ "$nodes" =~ ^[1-9][0-9]*$ ]] || { echo "--nodes must be a positive integer" >&2; exit 2; }
if [[ "$SCRIPT" == slurm_qwen_multinode.sbatch ]] && (( nodes < 2 )); then
  echo "slurm_qwen_multinode.sbatch needs --nodes >= 2" >&2; exit 2
fi
gpn="${O[gpus-per-node]:-$(prof gpus_per_node)}"; gtype="${O[gpu-type]:-$(prof gpu_type)}"
gres="${O[gres]:-}"
[[ -z "$gres" && -n "$gpn" ]] && gres="gpu:${gtype:+$gtype:}$gpn"
args=(--nodes="$nodes")
[[ -n "$nodelist" ]] && args+=(--nodelist="$nodelist")
[[ -n "${O[exclude]:-}" ]] && args+=(--exclude="${O[exclude]}")
for key in partition account time cpus_per_task mem; do
  flag="${key//_/-}"; val="${O[$flag]:-$(prof "$key")}"
  [[ -n "$val" ]] && args+=("--$flag=$val")
done
[[ -n "$gres" ]] && args+=(--gres="$gres")
[[ -n "${O[array]:-}" ]] && args+=(--array="${O[array]}")
export_str="ALL"
[[ -n "$PROFILE" ]] && export_str+=",PROFILE=$PROFILE"
for kv in "${EXPORTS[@]:-}"; do [[ -n "$kv" ]] && export_str+=",$kv"; done
args+=(--export="$export_str")
mkdir -p logs
cmd=(sbatch "${args[@]}" "${EXTRA[@]}" "scripts/hpc/$SCRIPT")
printf 'sbatch command:'; printf ' %q' "${cmd[@]}"; printf '\n'
(( DRY )) && exit 0
command -v sbatch >/dev/null || { echo "sbatch not found: run this on a Slurm login node (or use --dry-run)" >&2; exit 3; }
"${cmd[@]}"
