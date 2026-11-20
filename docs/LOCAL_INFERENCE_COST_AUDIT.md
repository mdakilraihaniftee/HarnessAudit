# Local inference cost audit

Scope: every code path in this repository (HarnessAudit + the SI-ATG attacker,
vendored ClawTeam, helper scripts) that can call a language model, or that sends
anything else over the network as a side effect of running agents.
Audit date: 2026-11-20. Repository state: `ad0214b` plus the local-inference changes.

**Result:** under the default `inference.mode: local_only`, no code path can reach
a commercial inference endpoint. Every model route resolves to a loopback (or
explicitly approved self-hosted) OpenAI-compatible server. Hosted paths still
exist, but they are refused before any process starts unless
`HA_INFERENCE_MODE=paid_opt_in` is set deliberately.

Re-run the audit at any time:

```bash
python -m multi_agent.local_inference audit                 # full report
python -m multi_agent.local_inference verify-cost           # exit 0 only if local-only and reachable
python -m multi_agent.local_inference verify-cost --static  # same, without probing the server
```

`verify-cost` exit codes: `0` verified local, `2` a route is billable or
unapproved, `3` a route is unresolved (server down / model not served),
`4` `paid_opt_in` is active.

## How the audit was done

1. Repository-wide search for model clients (`openai`, `anthropic`, `litellm`,
   Google/Gemini, Cohere, Mistral, Together, Groq, DashScope, Hugging Face
   inference, embeddings), credential/endpoint variables (`*_API_KEY`,
   `*_BASE_URL`), model-name defaults (`gpt-*`, `claude-*`, `sonnet`), and
   subprocesses that launch model CLIs (`claude`, `codex exec`, `openclaw`).
   Vendored ClawTeam makes no model calls itself (it only spawns CLIs).
   No embedding provider is used anywhere.
2. Every hidden default was traced: the CLI defaulted to `--harness claude`,
   `--model sonnet`, `--judge-model gpt-5.4`; the attacker config defaulted to a
   `codex` target on `gpt-5.6-sol`; the judge client read whatever
   `OPENAI_BASE_URL` happened to be set (or OpenRouter via `MASP_JUDGE_PROVIDER`).
3. The Qwen Code CLI (0.27.4) bundle was inspected for hosted features
   (DashScope / Qwen OAuth providers, `--fallback-model`, the "Advisor" model,
   usage statistics, auto-update, model-catalog refresh, managed auto-memory).
4. Egress was **measured**, not assumed: every `qwen_code` agent runs with a
   Node preload hook (`multi_agent/frameworks/native/qwen_code/assets/netlog.cjs`)
   that records the remote address of every TCP connection and every DNS lookup.
   In the verified fin-t1 runs the only connections were to `127.0.0.1:<vLLM port>`
   and `127.0.0.1:<finance MCP port>`, with zero DNS lookups.

## Call paths

| ID | Source | Purpose | Legacy model / provider | Can bill? | Auth | Silent fallback? | Local replacement | Status | Class |
|---|---|---|---|---|---|---|---|---|---|
| judge.completion | `completion_judge.py::_evaluate_llm_checkpoint` → `openai_json` | pooled LLM completion score (TCR) | gpt-5.4, OpenAI Responses API (or OpenRouter) | yes (legacy) | `OPENAI_API_KEY` / `OPENROUTER_API_KEY` | no fallback, but the default was hosted | route `completion_judge` → local vLLM, chat completions + JSON mode, T=0 | migrated | LOCAL_ONLY |
| judge.operational | `operational_judge.py::evaluate_operational_governance` | action-validity score (AVS) | gpt-5.4 | yes (legacy) | same | same | route `operational_judge` | migrated | LOCAL_ONLY |
| judge.perturbation | `perturbation_eval.py::_llm_subscores` | perturbation stability | gpt-5.4 | yes (legacy) | same | heuristic defaults on failure | route `perturbation_judge` | migrated | LOCAL_ONLY |
| judge.probe_scripts | `scripts/probe_qwen_judge.py`, `scripts/run_qwen_judge.py` | OpenRouter-hosted Qwen judge demos | qwen/qwen3-8b @ OpenRouter | yes | `OPENROUTER_API_KEY` | no | `local_inference health` | kept, refused under local_only | PAID_OPT_IN |
| harness.claude | `frameworks/native/claude/runtime.py` | ClawTeam agents via Claude Code | sonnet | yes | `ANTHROPIC_API_KEY` / login | uses cached login if no key | `--harness qwen_code` | kept, refused under local_only | PAID_OPT_IN |
| harness.codex | `frameworks/native/codex/runtime.py` | ClawTeam agents via `codex exec` | gpt-5.x | yes | `OPENAI_API_KEY` / `~/.codex` | auto-detects key vs login | `--harness qwen_code` | kept, refused under local_only | PAID_OPT_IN |
| harness.openclaw | `frameworks/native/openclaw/*`, `single_agent` | OpenClaw agents | gpt-5.4 / claude via OpenRouter | yes | `OPENROUTER_API_KEY` | no | `--harness qwen_code` (multi-agent) | kept, refused under local_only | PAID_OPT_IN |
| harness.qwen_code | `frameworks/native/qwen_code/*` (new) | ClawTeam coordinator + specialists | — | **no** | placeholder key | none (see below) | — | new default | LOCAL_ONLY |
| framework.oai | `frameworks/oai/adapter.py` | OpenAI Agents SDK runtime | gpt-* / litellm anthropic, gemini | yes | provider keys | model-name heuristics route to litellm providers | not migrated | refused under local_only | PAID_OPT_IN |
| framework.adk | `frameworks/adk/*` | Google ADK over the OpenAI SDK | gpt-* | yes | `OPENAI_API_KEY` | no | not migrated | refused under local_only | PAID_OPT_IN |
| attacker.codex_cli | `attacks/agent.py::CodexCliBackend` | SI-ATG candidate generation | Codex default | yes | Codex login / key | no | `qwen_code` / `local_openai` backends | kept, refused under local_only | PAID_OPT_IN |
| attacker.qwen_code | `attacks/qwen_backend.py::QwenCodeAttackerBackend` (new) | SI-ATG generation via headless Qwen Code | — | **no** | placeholder | none | — | new | LOCAL_ONLY |
| attacker.local_openai | `attacks/qwen_backend.py::LocalOpenAIAttackerBackend` (new) | SI-ATG generation, server-side JSON schema | — | **no** | placeholder | none | — | new | LOCAL_ONLY |
| attacker.reflector | `attacks/agent.py::AttackerAgent._reflect` | reflection text | none (deterministic) | no | — | — | route reserved | unchanged | NON_MODEL |
| attacker.harness_optimizer | `attacks/agent.py::AttackerAgent._optimize_harness` | strategy-weight promotion | none (deterministic) | no | — | — | route reserved | unchanged | NON_MODEL |
| attacker.mock_control | `MockBackend`, `ControlBackend`, `StrategySelector` | demos, fixed controls B1/B2 | none | no | — | — | — | unchanged | NON_MODEL |
| telemetry.openai_agents | `oai/adapter.py` | Agents SDK trace export | api.openai.com/traces | no (not inference) | key | — | already disabled (`set_tracing_disabled`) | unchanged | NON_MODEL |
| telemetry.qwen_code | Qwen Code CLI | usage statistics, auto-update, catalog refresh, auto-memory/dream/skill | Alibaba endpoints / model | no (egress only) | — | — | disabled (env + settings + `--bare` + `--advisor off`) | disabled | NON_MODEL |
| telemetry.vllm | vLLM | usage stats | stats endpoint | no | — | — | `VLLM_NO_USAGE_STATS=1`, `DO_NOT_TRACK=1` | disabled | NON_MODEL |
| download.hf_weights | `03_setup_qwen.sh`, vLLM start-up | weight download | huggingface.co | no (not inference) | optional `HF_TOKEN` | vLLM would download a missing model | setup needs `--allow-download`; server runs with `HF_HUB_OFFLINE=1` | gated | NON_MODEL |
| scripts.legacy_runners | `run_ma.sh`, `rc/project.env`, `run_codex_benchmark.py`, `si_atg_experiment_scripts/04_*` | batch runners | gpt-5.4 / gpt-5.6-sol | yes (legacy) | keys | no | `scripts/local_qwen/*` | every run passes the CLI cost guard | PAID_OPT_IN |

No path is classified `UNKNOWN`. The router classifies any endpoint that is not
loopback and not in `inference.approved_endpoints` as `UNKNOWN`, and `UNKNOWN`
fails closed exactly like `COMMERCIAL`.

## Guard mechanics (local_only)

* **One resolver.** `multi_agent/local_inference/router.py::resolve_route` is the
  only place routes are resolved. It never reads `OPENAI_BASE_URL`/`OPENAI_API_KEY`.
  Known commercial hosts (OpenAI, Azure OpenAI, Anthropic, OpenRouter, DashScope /
  Model Studio, Qwen portal, Google, HF inference, Together, Fireworks, Groq,
  DeepSeek, Mistral, Cohere, xAI, Perplexity, Bedrock, …) are always
  `COMMERCIAL`, even if someone lists them under `approved_endpoints`.
* **Refused before start.** `multi_agent run` (and the SI-ATG executor, which goes
  through the same `build_run_context`) calls `check_run_policy` first: hosted
  harnesses (`claude`, `codex`, `openclaw`), hosted frameworks (`oai`, `adk`), a
  non-local judge model and a non-served target model are all `CostGuardError`s.
  The attacker CLI calls `check_attacker_backend` (refuses `codex_cli`).
  `single_agent` (OpenClaw/OpenRouter only) refuses to run.
* **No fallback.** If the local server is down the run fails before any agent
  starts ("is not being served … No hosted fallback is used"). Judge calls raise
  `LocalEndpointUnavailable`, a type the judges do **not** swallow (they used to
  turn `OpenAIJSONError` into a silent 0.0 score). A judge request for a model the
  local route does not serve (e.g. the old default `gpt-5.4`) raises
  `CostGuardError` before any client object is created.
* **Child processes.** Every Qwen Code agent and the attacker CLI get an
  environment in which all commercial credential/endpoint variables are blanked
  (`OPENAI_*`, `ANTHROPIC_*`, `OPENROUTER_*`, `DASHSCOPE_API_KEY`, `GEMINI_API_KEY`,
  `TAVILY_API_KEY`, …), `OPENAI_BASE_URL` set to the local route, a placeholder
  key, an isolated `QWEN_HOME`, `--auth-type openai`, explicit
  `--openai-base-url`, `--bare`, `--advisor off`, no `--fallback-model`, and
  `web_search`/`web_fetch` excluded. The vLLM server environment drops those
  variables entirely.
* **Credentials in the environment are fine.** With the repository `.env`
  (real OpenAI/OpenRouter keys) loaded, `verify-cost` still returns
  `LOCAL_ONLY_VERIFIED`; the audit reports that the variables are present
  (never their values) and unused.
* **Placeholder keys** (`EMPTY`, `local-no-key`, `...`, `sk-no-key-required`, …)
  are recognised and never reported as commercial credentials.
* **`allow_paid_fallback: true` with `local_only`** is rejected as a contradiction.

## Verification evidence

| Check | Result | Evidence |
|---|---|---|
| default config resolves to local | PASS | `tests/test_local_inference.py::test_default_config_is_local_only_and_all_routes_local` |
| commercial keys in env never selected | PASS | `test_commercial_keys_in_env_are_never_selected`; `verify-cost` with `.env` loaded → `LOCAL_ONLY_VERIFIED` |
| missing local server → failure, no fallback | PASS | `test_missing_local_server_blocks_run_before_agents`, `test_unavailable_local_judge_raises_without_hosted_fallback`, `scripts/tests/test_cost_guard.sh` |
| judges cannot use paid APIs | PASS | `test_judge_cannot_silently_use_hosted_model` (no client constructed) |
| attacker/target use their routes | PASS | `test_attacker_and_target_use_their_assigned_routes` |
| paid endpoints need explicit opt-in | PASS | `test_paid_harness_allowed_only_with_explicit_opt_in`; CLI checks in `test_cost_guard.sh` |
| placeholder keys not flagged | PASS | `test_placeholder_keys_are_not_billing_warnings` |
| every endpoint in the audit report | PASS | `test_audit_report_lists_every_route_and_hides_secrets` |
| real agent egress loopback-only | PASS | per-agent `netlog.jsonl` in each run's `native/` artifacts; `verify_clawteam_run.py` criterion 11 |

## What `paid_opt_in` re-enables

Exactly the legacy behaviour: `--harness claude|codex|openclaw`, `--framework
oai|adk`, `--backend codex_cli`, OpenAI/OpenRouter judges via `MASP_JUDGE_PROVIDER`,
`single_agent`, and the legacy defaults (`claude` harness, `sonnet`, `gpt-5.4`
judge). The legacy SI-ATG configuration is preserved as
`multi_agent/attacks/configs/finance_paid.yaml`. `rc/project.env` SMOKE/FULL
commands (OpenClaw/Codex) therefore need `HA_INFERENCE_MODE=paid_opt_in` at
submission time; without it they stop with a `CostGuardError` instead of billing.
