# BACH — target capability decision (can Qwen3-8B support the study?)

## Current answer: **unresolved, pending one specific experiment (T3C)**

| Question | Evidence so far | Status |
|---|---|---|
| Was the T3 context defect repaired? | CPU: stored assignment, MCP response and the real Qwen CLI trace all carry the authorized IDs before the first finance call (3/3 roles) | **Repaired (CPU-verified); real run NOT_EXECUTED** |
| Can the risk officer do its work? | R4 full team: its own requests lowered all three limits in 0/15 trials. T3: invalid. | **Unknown in isolation**; full-team evidence is negative |
| Can the trader do its work? | R4: 46 authorized committed trades, but races preempted 26. T3: invalid. | **Unknown in isolation** |
| Can compliance do its work? | R4: 15/15 scoped reports committed by compliance. T3: invalid. | **Unknown in isolation**; full-team evidence is positive |
| Do agents respect roles in isolation? | Not measured (T3 had no actions) | **Unknown** |
| Can the coordinator discover tasks? | T2: 7/20 sessions saw all assignments; R4 schema 8/10 high-priority first calls vs R3 0/10; discovery 3/10 vs 4/10 | **Unreliable (descriptive)** |
| Does the full team complete legitimate work? | R4: 0/15 deterministic core; 0/5 B0 | **No** |
| Is a stronger model necessary? | Not decidable before T3C | **Open** |
| Is Phase 2 ready? | G5 FAIL; G2, G3, G7, G8 BLOCKED | **No** |

## Classification of causes (current evidence)

| Candidate | Status |
|---|---|
| Diagnostic setup defect | **Yes for the original T3** (fixed); also the T2 wording confound (documented, with a corrected transform) |
| Coordinator/tool-use weakness | **Supported:** discovery 7/20 in isolation, 0/15 in the team. Duplicate-task creation 7/20 isolated, 9/15 in the team. |
| Specialist model limitation | **Plausible, not yet isolated.** The R4 risk-officer values and role crossing are team-run evidence and confounded by peers. |
| Broader model limitation | Possible; depends on T3C |
| Mixed causes | Most likely, given the current evidence (inference) |

Overall: **mixed / unresolved.**

## Decision rule for after T3C (pre-specified; descriptive, n=5 per role)

* **Case A: all three roles succeed in isolation**, meaning task completion ≥4/5 with context delivered and no out-of-role effects.
  * **Reading:** the bottleneck is coordination and interaction.
  * **Recommend:** a narrowly scoped coordination-interface study under a new identity, needing PI approval. Candidates: filtered-empty hint, truthful schema presentation, or a progress affordance. No role enforcement.
* **Case B: two or more roles fail** (completion ≤1/5, NO_ACTION, invented IDs, false completion, or out-of-role effects), with context delivered.
  * **Reading:** a model-capability limitation.
  * **Recommend:** a separately versioned stronger-model comparison (below), not prompt tuning.
* **Case C: mixed.**
  * **Recommend:** name the failing role, then run the smallest discriminating test for that role. Example: if only the risk officer fails, compare the same fixture with the task text unchanged against an identical stronger-model session to separate task difficulty from model capability.
* **Case D: diagnostic invalid** (any `NO_OPPORTUNITY` or `MISSING_EVIDENCE`).
  * **Recommend:** repair and do not interpret.
* **Case E: the only paths forward change prompts, tools, orchestration, model or task.**
  * **Recommend:** stop and request a PI decision.

## Stronger-model decision support (§14). Proposal only; nothing downloaded or switched.

**Locally cached:** only `Qwen/Qwen3-8B`, the pinned revision. No larger Qwen3 checkpoint is cached, so any comparison needs a PI-approved download.

**Common facts.** Qwen3 open-weight models are published under Apache-2.0; verify on the model card at download time. Larger Qwen3 models share the 8B's chat template and Hermes-style tool calls. They are supported by the same vLLM and `--tool-call-parser hermes` and work with the same Qwen Code CLI, but this must be verified with the existing schema-capture and stub tests before any run.

| Candidate | Weights (bf16, approx.) | Fit on 4× L40S (46 GB, 0.80 util) | Serving layout | Notes |
|---|---|---|---|---|
| Qwen3-14B | ~30 GB | TP=1 leaves ~7 GB KV (about one 32k sequence per replica) | prefer 2 replicas × TP=2 | smallest step up |
| Qwen3-32B | ~66 GB | TP=2 leaves ~8 GB KV per GPU (tight) | 1 replica × TP=4 (~53 GB KV) | strongest dense option that fits |
| Qwen3-30B-A3B (MoE) | ~61 GB | TP=2 tight; TP=4 comfortable | 1 replica × TP=4 | fast decoding; different architecture class |

**Comparison plan:**

1. **New identity**, for example `TARGET_V1R4-M14`. Model and revision change; the task, checker, tool permissions, role prompts, R4 assignment builder, sampling (0.7 / 0.8 / 20, thinking off), 32,768 context, 80 turns, 300 s and 400 requests stay the same.
2. **Same CPU checks:** schema capture and stub tests with the new model name.
3. **Same diagnostics:** identical T3C fixtures (same fixture identity), then T2 urgency-free, then one full-team B0 smoke.
4. **Comparison:** against the Qwen3-8B T3C results. Never pool. A larger model is not assumed to fix coordination.
5. **Recording:** the serving-layout change (TP/replicas) is recorded as part of the new identity.
