# BACH — Phase 1 → Phase 2 decision

> **Superseded (2026-11-23, pre-Phase-2 cycle):** T2/T3 have run; the original T3 had a diagnostic context defect. Current guidance: [BACH_FINAL_MANUAL_RUN_GUIDE.md](BACH_FINAL_MANUAL_RUN_GUIDE.md) and [BACH_PHASE2_ENTRY_DECISION.md](BACH_PHASE2_ENTRY_DECISION.md).

## Status

**Phase 1: PARTIALLY COMPLETE. Readiness: NO-GO. Phase 2 is not ready and was not started.**

Engineering and measurement requirements are met:

* the independent audit (48/48 reported values reconcile);
* instrumentation controls (G1 PASS);
* effect ledger validity (G6 PASS);
* model-visible task context (G4 PASS);
* the observation-only coordination diagnostic;
* the scripted legal workflow (core completion with zero checker matches);
* isolated diagnostics ready for manual launch;
* a validated reviewer bundle.

What is not met is the purpose of Phase 1: **the trusted Qwen3-8B target does not perform its legitimate task.** In the 15-trial benign study:

* G5 is 0/5 for B0 and 0/15 overall;
* the coordinator finds none of the seeded assignments in 15/15 trials;
* every specialist executes every role.

## Why no further target change was made

* **§25.1:** the task-board interface and schema are verified correct, and Qwen repeatedly misuses them.
* **§25.8:** the known coordination failure persists after the justified, controlled R4 schema repair, now with a different over-filter (`priority="high"`).
* **§25.4 / 25.5 would follow:** remaining candidate fixes for role crossing need mechanical enforcement, changes to the protected task context, or a new coordination interface.

## Decisions requested from the PI

| # | Decision | Recommendation |
|---|---|---|
| 1 | Approve running T2 (isolated coordinator, 20 sessions) and T3 (isolated specialists, 15 sessions) on GPUs 0–3 | **Yes, first.** Local, unchanged model and limits, ≤ 14,000 requests in total, about 1 hour. These are the most informative next tests. |
| 2 | Stronger local model comparison (§19) | Decide after T2/T3. No stronger Qwen checkpoint is cached locally (only `Qwen/Qwen3-8B`), so this needs an approved download and a new identity (for example `TARGET_V1R4-M`). On 4× L40S (46 GB each): Qwen3-14B fits TP=1 in bf16; Qwen3-32B needs TP=2 in bf16 (or a quantized checkpoint at TP=1). Keep task, tools, prompts, policies, checker and limits fixed, run T2/T3 then a B0 smoke, and never pool with Qwen3-8B results. Verify the license on the model card at download time. |
| 3 | New coordination affordance (filtered-empty hint) or stop publishing the `priority` enum | Only if T2 isolates a schema effect. Either change needs a new identity. |
| 4 | Role framing: the full-team checklist inside each specialist's protected task context | PI judgement. Changing it alters the protected task context and the research question. Do not change it silently. |
| 5 | Outcome formulation (G8) | O1 is saturated under benign conditions (15/15). If that holds for a working target, revisit the outcome definitions before Phase 2. |
| 6 | 45-trial calibration and concurrency benchmarks | Not until a target passes the unchanged smoke. |

## Smallest remaining blocker and most informative next test

* **Blocker: G5** (legitimate completion).
* **Next test: T2**, then T3:

```bash
cd /home/ai5377/HarnessAudit && RUN_REAL=YES bash scripts/research_phase1/phase1_completion.sh test-coordination
cd /home/ai5377/HarnessAudit && RUN_REAL=YES bash scripts/research_phase1/phase1_completion.sh test-specialists
```

T2 distinguishes interface side-effect, wording cue and model limitation for the coordinator. T3 distinguishes intrinsic whole-workflow behaviour from team interaction for the specialists. A failed but well-observed result is preferable to a tuned "pass".
