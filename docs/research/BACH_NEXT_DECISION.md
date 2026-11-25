# BACH — next PI decision after the TARGET_V1R4 preparation

## Where things stand

* A genuine Level 1 defect was found and repaired: the ClawTeam MCP tools exposed
  enum-coerced arguments and the exact-match `owner` filter to Qwen3-8B as undocumented
  free strings, and rejections did not list accepted values (Decision **A**).
* The same evidence carries a **B** risk: in 5/5 historical coordinator sessions,
  across three prompt versions, Qwen3-8B's first action was the same comma-joined owner
  query, and in v4 it never used the explicit unfiltered example, other read-only board
  tools, or its three waiting mailbox reports. Whether a truthful schema changes that is
  unknown until a real run.
* Independently of the coordinator, specialists started in parallel, crossed roles and
  raced (7 verified unauthorized effects in clean B0). Those failures are observable by
  design and remain so. They are also why the deterministic core failed. A coordination
  fix alone may therefore not produce a passing smoke (Decision **C** risk).

## Next experiment (user decision, 2026-11-23)

**Run the TARGET_V1R4 benign study once: 5 trials each for B0, B1n and B1p, with every
trial kept.** This gives a small sample for the one repaired component instead of the
single trial the fail-fast smoke would produce. The unchanged smoke can still be run
separately; it remains the acceptance gate for any later calibration.

```bash
cd /home/ai5377/HarnessAudit && bash scripts/research_phase1/phase1r4_manual.sh gpu-preflight && bash scripts/research_phase1/phase1r4_manual.sh benign-study
```

Pre-specified reading, from `r4_study_summary.json` and each trial's
`derived/<trial>/coordinator_call_ledger.json`. With n = 5 per condition this is a
descriptive count, not an estimated rate:

| Observation (benign study; smoke if run) | Interpretation | Next step |
|---|---|---|
| Most coordinators still open with a comma-joined owner and end in `NO_PROGRESS_LOOP` / turn limit | Schema documentation was insufficient; model-capability limitation (**B → Level 3**) | Stop prompt/interface iteration; PI decides on a separately versioned stronger local coordinator model test |
| Coordinator uses valid filters or an unfiltered query, communicates and returns a final response, but core still fails because specialists cross roles / race | Interface repair worked; orchestration/role behaviour is the blocker (**C**) | Phase 2 not ready; PI decides whether sequencing/role restriction is in scope (**E**: it changes the research question) |
| B0 trials complete observation and the deterministic core, and the unchanged smoke also passes | (**D**) | Preserve; report role violations and effects; ask PI to approve the existing 45-trial benign calibration (not started automatically) |
| Infrastructure, observation or cleanup failure | Not interpretable as model behaviour | Diagnose from the bundle; never rerun or replace trials in the same folder |

## If Level 3 is reached (proposal only — not implemented)

Keep task, prompts, tools, checker and limits fixed and change only the target model,
as a new identity (e.g. TARGET_V1R4-M). Candidates that fit 4× L40S (48 GB) locally
without downloads would need PI approval and an explicit cached-weights step. Measure
the coordinator's first-call filter, loop label and final-response rate on the same
smoke before any calibration. A model change alters the system under study and must not
be pooled with Qwen3-8B results.

## Not recommended

Another prompt appendix; raising the 80-turn, timeout or request limits; enabling
mechanical role enforcement; hidden coordinator call rewriting; any 45-trial,
concurrency, B2/B3, Phase 2 or Bayesian BACH run before a V1R4 smoke passes. Pooling the
benign study with earlier target versions.
