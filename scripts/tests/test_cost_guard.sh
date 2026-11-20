#!/usr/bin/env bash
# test_cost_guard.sh — end-to-end cost-guard checks through the real CLIs.
# Fake commercial credentials are exported to prove local_only never uses them.
# No GPU or server is needed (endpoint probing is disabled where it would matter).
# Usage: scripts/tests/test_cost_guard.sh
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../local_qwen/lib.sh"
ha_require_python
unset HA_INFERENCE_MODE
export OPENAI_API_KEY="sk-test-$(printf 'x%.0s' {1..40})" OPENAI_BASE_URL=https://api.openai.com/v1
export OPENROUTER_API_KEY="sk-or-v1-fake" ANTHROPIC_API_KEY="sk-ant-fake" DASHSCOPE_API_KEY="sk-fake"
pass=0; fail=0
check() {  # check NAME EXPECTED_EXIT GREP_PATTERN -- command...
  local name="$1" want="$2" pattern="$3"; shift 4
  local out rc
  out=$("$@" 2>&1); rc=$?
  if [[ "$rc" == "$want" || ( "$want" == nonzero && "$rc" != 0 ) ]] && grep -Eq -- "$pattern" <<<"$out"; then
    echo "PASS  $name (exit $rc)"; pass=$((pass+1))
  else
    echo "FAIL  $name (exit $rc, wanted $want /$pattern/)"; echo "$out" | tail -5 | sed 's/^/      /'; fail=$((fail+1))
  fi
  if grep -q "sk-test-xxxx" <<<"$out"; then echo "FAIL  $name leaked a credential value"; fail=$((fail+1)); fi
}
T=multi_agent/tasks/finance/financial_planning/fin-t1.yaml
check "default mode is local_only" 0 "local_only" -- ha_li config --get inference.mode
check "static verify-cost passes with commercial keys present" 0 "LOCAL_ONLY_STATIC_OK" -- ha_li verify-cost --static
check "credentials reported, never printed" 0 "OPENAI_API_KEY" -- ha_li audit --static
check "hosted judge route is billable" 2 "POTENTIALLY_BILLABLE" -- ha_li verify-cost --static --set routes.completion_judge.endpoint=https://openrouter.ai/api/v1
check "unapproved network endpoint fails closed" 2 "not loopback" -- ha_li verify-cost --static --set inference.endpoint=http://10.0.0.5:8000/v1
check "approved self-hosted endpoint accepted" 0 "SELF_HOSTED_APPROVED" -- ha_li verify-cost --static --set inference.endpoint=http://10.0.0.5:8000/v1 --set 'inference.approved_endpoints=["10.0.0.5:8000"]'
check "paid_opt_in is reported (non-zero)" 4 "PAID_OPT_IN_ACTIVE" -- env HA_INFERENCE_MODE=paid_opt_in "$HA_PYTHON" -m multi_agent.local_inference verify-cost --static
check "codex harness refused" nonzero "CostGuardError|launches a CLI that calls a hosted" -- "$HA_PYTHON" -m multi_agent run "$T" --harness codex --skip-judge
check "claude harness refused" nonzero "hosted model provider" -- "$HA_PYTHON" -m multi_agent run "$T" --harness claude --skip-judge
check "oai framework refused" nonzero "has no local route" -- "$HA_PYTHON" -m multi_agent run "$T" --framework oai --skip-judge
check "hosted judge model refused" nonzero "not the local judge route model" -- env HA_SKIP_ENDPOINT_PREFLIGHT=1 "$HA_PYTHON" -m multi_agent run "$T" --harness qwen_code --judge-model gpt-5.4
check "missing local server blocks the run" nonzero "is not being served" -- env HA_LOCAL_ENDPOINT=http://127.0.0.1:9/v1 "$HA_PYTHON" -m multi_agent run "$T" --harness qwen_code --skip-judge
check "codex attacker backend refused" nonzero "calls a hosted provider" -- "$HA_PYTHON" -m multi_agent.attacks campaign --task "$T" --backend codex_cli --rounds 1 --dry-run --output-dir "$(mktemp -d)" --memory-db "$(mktemp -u).sqlite"
check "single_agent (OpenRouter) refused" nonzero "OpenRouter, which is billable" -- "$HA_PYTHON" -m single_agent run single_agent/tasks/daily_life/sa-dl-t1.yaml
check "inference.allow_paid_fallback contradiction refused" 2 "contradicts" -- ha_li verify-cost --static --set inference.allow_paid_fallback=true
echo "cost guard: $pass passed, $fail failed"
(( fail == 0 ))
