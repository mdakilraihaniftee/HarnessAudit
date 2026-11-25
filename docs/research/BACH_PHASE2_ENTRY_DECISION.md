# BACH — Phase 2 entry decision

## Phase 2 is not ready. No part of Phase 2 (attacks, SI-ATG loops, Bayesian BACH) should begin.

**Reasons:**

* **[Evidence]** G5 FAIL (0/5 B0 core completion); G2, G3, G7 and G8 BLOCKED.
* **[Evidence]** The target does not perform its legitimate task. Excess-risk Δ_r(π) needs a functioning benign baseline P₀, so it cannot be attributed.
* **[Evidence]** O1 is saturated under benign conditions (15/15), leaving no headroom for a requester-induced increase.

## Recommended PI decision: **C now, then A or B**

* **C — perform one additional specific capability experiment: T3C.**
  * **[Implementation]** The corrected isolated specialist diagnostic is built and CPU-validated: authorized context reaches the model's trace, prerequisites are role-specific, and no answer leakage was found.
  * **[Inference]** It is the only remaining test that separates specialist capability from coordination. Neither the original T3 (invalid context) nor the R4 team runs (peers confounded) can.
  * **Cost:** 3 + 15 sessions, ≤ 7,200 requests, on the same model and limits.
* **Then, using the pre-specified reading** in [BACH_TARGET_CAPABILITY_DECISION.md](BACH_TARGET_CAPABILITY_DECISION.md):
  * **A — retain Qwen3-8B and investigate coordination** if the specialists succeed in isolation;
  * **B — test a stronger model under a new identity** if they fail with correct context. No larger Qwen3 is cached, so this needs download approval.
* **D — reconsider the target or formulation** if a working target still saturates O1, or if neither A nor B yields a functioning baseline.

## What this cycle did NOT do

* No change to TARGET_V1R4. The identity is verified: `213a034d…49cf8`.
* No change to prompts, the checker, policies, the task, the model, sampling or limits.
* No prompt tuning, answer hints, role enforcement or call rewriting.
* No GPU run: T3C is prepared for manual launch.

## Approvals requested

1. Run the T3C smoke, then the T3C diagnostic (GPUs 0–3, local).
2. Optional: the T2 urgency-only follow-up. Not needed for the next decision.
3. After T3C: A, B or D as above. B additionally needs approval to download a larger Qwen3 checkpoint and to freeze a new target identity.
