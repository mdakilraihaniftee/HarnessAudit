# BACH next research roadmap

Updated sequence after failed v3: prepare separately identified TARGET_V1R3;
manually run fresh smoke v4; only after a same-identity pass, collect 15 trials
each for B0/B1n/B1p (45 total) at concurrency one. Export and review the actual
results before later phases. See `BACH_TARGET_V1R3_IMPLEMENTATION.md` for scripts,
provenance and stop behavior. No GPU inference was launched during preparation.
The list below describes the broader conditional roadmap.

1. Review the reconciled original-pilot findings and Phase 1R source/controls.
   Treat the historical validity exclusions and nonexistent-ID findings as
   measurement corrections, not revised historical outcomes.
2. Inspect GPU allocation and launch the benign B0/B1n/B1p smoke manually.
   Stop on observation failures; inspect MCP, context, state evidence and cleanup.
3. Run configurable benign calibration and separately concurrency 2 and 4 on
   explicitly authorized idle GPUs. Compare reliably observed trials/hour,
   requests/second, target time, tool errors, validity, memory and cleanup.
4. Evaluate G1–G8. Current real diagnostic gates are untested; there is no Phase 2
   GO based on CPU tests alone. Preserve all attempts and unknown outcomes.
5. PI resolves D1–D5 and the numeric-suitability interpretation. D1: retain or
   add cross-role responsibility instructions? D2: retain Qwen3-8B or separately
   compare a stronger model based on legitimate reliability? D3: correct examples
   only or explicitly approve bare-name aliases? D4: choose viable prespecified
   O1/O2/O3 and frozen-set versus generator estimand? D5: include both target
   versions with their historical reproducibility limitations?
6. Prompt 2 may implement the approved five-arm development/confirmation workflow,
   blind note annotations, frozen artifacts, intention-to-test comparisons,
   multiplicity/unknown sensitivity and cost planning. It must use actual Phase
   1R diagnostic results. No attack confirmation is run in Prompt 1.
7. Bayesian BACH optimization remains conditional on a confirmed, meaningful,
   non-saturated implicit requester-induced excess-risk result. B2-only effects
   require a scientific framing decision, not automatic escalation to BACH.
