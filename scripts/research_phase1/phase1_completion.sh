#!/usr/bin/env bash
# BACH Phase 1 completion launcher (manual). CPU commands never touch GPUs or models.
# Real-inference commands (t3-smoke, t3-diagnostic, t2-followup, smoke-benign) require
# RUN_REAL=YES, a fresh output folder, and never retry or replace a failed session.
# Model, sampling, thinking, context, turn, timeout and request limits are unchanged.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
MODE="${1:-}"
PY="$ROOT/.venv/bin/python"
OUT=results/research_phase1c
GPU_IDS="${GPU_IDS:-0,1,2,3}"
OUT_TAG="${OUT_TAG:-v1}"
export HA_INFERENCE_MODE=local_only HA_ALLOW_MODEL_DOWNLOAD=false
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
unset OPENROUTER_API_KEY OPENAI_API_KEY ANTHROPIC_API_KEY 2>/dev/null || true

cli() { "$PY" -m multi_agent.research_phase1c "$@"; }

require_real() {
  if [[ "${RUN_REAL:-}" != "YES" ]]; then
    echo "Real Qwen inference on GPUs $GPU_IDS. Re-run with RUN_REAL=YES to confirm." >&2
    exit 2
  fi
  if [[ ! "$GPU_IDS" =~ ^[0-9]+(,[0-9]+)*$ ]]; then
    echo "GPU_IDS must be a comma-separated list of indices" >&2
    exit 2
  fi
}

blocked() {
  echo "BLOCKED: $1" >&2
  exit 3
}

isolated() {
  local test="$1" dir="$2" repeats="${3:-5}"
  require_real
  if [[ -e "$dir" ]]; then
    echo "Refusing to reuse $dir (no retries or replacements). Set OUT_TAG=v2 etc." >&2
    exit 2
  fi
  local rc=0
  RUN_REAL=YES cli isolated --test "$test" --output "$dir" --repeats "$repeats" --parallel 4 \
    --gpu-ids "$GPU_IDS" || rc=$?
  printf '{"mode":"%s","exit_code":%s,"retries":0,"output":"%s"}\n' "$test" "$rc" "$dir" \
    > "$dir/manual_launch_receipt.json" 2>/dev/null || true
  cli audit-r4 >/dev/null || true
  cli audit-t2-t3 >/dev/null || true
  cli analyze-isolated --root "$dir" >/dev/null || true
  cli evaluate-gates || true
  cli package || true
  return "$rc"
}

case "$MODE" in
  audit-r4)            cli audit-r4 ;;
  test-task-board)     cli test-task-board ;;
  test-instrumentation) cli test-instrumentation ;;
  test-scripted-workflow) cli test-scripted-workflow ;;
  test-cpu)
    "$PY" -c "from multi_agent.research_phase1c.bundle import record_tests; import json; \
r = record_tests(); print(json.dumps([(x['name'], x['passed'], x['summary_line']) for x in r['results']], indent=1)); \
raise SystemExit(0 if r['all_passed'] else 1)"
    ;;
  test-isolated-stub)
    # CPU plumbing check of the isolated runners: real Qwen Code CLI, stub endpoint, no GPU.
    dir="${STUB_DIR:-$(mktemp -d)/stub}"
    cli isolated --test coordinator --output "$dir/coordinator" --repeats 1 --stub
    cli isolated --test specialists --output "$dir/specialists" --repeats 1 --stub
    cli isolated --test specialists_corrected --output "$dir/specialists_corrected" \
      --repeats 1 --stub
    ;;
  audit-t2-t3)         cli audit-t2-t3 ;;
  analyze-t2)          cli analyze-t2 ;;
  analyze-t3)
    for d in "$OUT"/isolated/t3*/; do
      [[ -f "$d/plan.json" ]] && cli analyze-isolated --root "$d"
    done ;;
  validate-t3-context) cli validate-t3-context ;;
  test-t3-fixtures)
    "$PY" -m pytest -q -p no:cacheprovider tests/test_phase1_completion.py -k "t3c or t2t3" ;;
  cpu-all)
    # One CPU-only command: T2/T3 reconstruction, corrected fixtures, identifiers,
    # model-visible payloads (real Qwen CLI vs stub), state preconditions, tests, identity.
    cli audit-t2-t3
    cli validate-t3-context
    "$PY" -m pytest -q -p no:cacheprovider tests/test_phase1_completion.py
    cli identity-check ;;
  t3-smoke)
    isolated specialists_corrected "$OUT/isolated/t3c_smoke_$OUT_TAG" 1 ;;
  t3-diagnostic)
    require_real
    smoke="${T3C_SMOKE_DIR:-$OUT/isolated/t3c_smoke_$OUT_TAG}"
    if ! cli t3c-ready --smoke "$smoke"; then
      echo "Refusing: no structurally valid real T3C smoke at $smoke (set T3C_SMOKE_DIR)." >&2
      exit 2
    fi
    isolated specialists_corrected "$OUT/isolated/t3c_diagnostic_$OUT_TAG" 5 ;;
  t2-followup)
    # Optional, one factor (urgency wording only; schema R4; corrected builder), n=10/arm.
    isolated coordinator_urgency "$OUT/isolated/t2u_urgency_$OUT_TAG" 10 ;;
  identity-check)      cli identity-check ;;
  verify-preservation) cli verify-preservation ;;
  gpu-preflight)       cli gpu-preflight --gpu-ids "$GPU_IDS" ;;
  test-coordination|test-specialists)
    blocked "Superseded: the original T2/T3 runners seeded assignments without the R4
protected task context (diagnostic defect). Use t3-smoke / t3-diagnostic (corrected T3)
or the optional t2-followup." ;;
  smoke-b0)
    blocked "TARGET_V1R4 accepts only 'smoke' (B0,B1p,B1n, fail-fast) and 'benign-study'. A
B0-only mode would change the frozen controller and therefore the target identity, and no
target change is justified by the evidence. Run t3-smoke/t3-diagnostic first;
then the PI may approve a new release with a B0-only mode." ;;
  smoke-benign)
    require_real
    echo "Unchanged TARGET_V1R4 three-arm smoke (writes diagnostic_smoke/v5)." >&2
    echo "R4 evidence predicts core failure (0/15); see BACH_PHASE1_TO_PHASE2_DECISION.md." >&2
    exec bash scripts/research_phase1/phase1r4_manual.sh smoke ;;
  calibration)
    blocked "The 45-trial benign calibration needs a passing smoke and explicit PI approval;
the TARGET_V1R4 controller refuses 'diagnostic' for this target. G5 is 0/5." ;;
  concurrency)
    blocked "Concurrency-2/4 benchmarks need a passing smoke and PI approval; the
TARGET_V1R4 controller refuses 'benchmark' for this target." ;;
  analyze)
    cli audit-r4
    for d in "$OUT"/isolated/*/; do
      [[ -f "$d/plan.json" ]] && cli analyze-isolated --root "$d" >/dev/null
    done
    cli evaluate-gates ;;
  evaluate-gates)      cli evaluate-gates ;;
  package|export)      cli package ;;
  *)
    echo "usage: $0 {audit-r4|test-task-board|test-instrumentation|test-scripted-workflow|" >&2
    echo "  test-cpu|test-isolated-stub|identity-check|verify-preservation|gpu-preflight|" >&2
    echo "  test-coordination|test-specialists|smoke-b0|smoke-benign|calibration|" >&2
    echo "  concurrency|analyze|evaluate-gates|package|audit-t2-t3|analyze-t2|analyze-t3|" >&2
    echo "  validate-t3-context|test-t3-fixtures|cpu-all|t3-smoke|t3-diagnostic|t2-followup}" >&2
    exit 2 ;;
esac
