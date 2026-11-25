# BACH — Phase 1 completion audit (overview)

**Overall: PARTIALLY COMPLETE. Readiness: NO-GO.** No new real inference was run in this phase, and no new target identity was created. TARGET_V1R4 (`213a034d…49cf8`) remains current and its identity still verifies.

## Stages

| Stage | Result | Status label | Report |
|---|---|---|---|
| P0 Independent R4 audit | 48/48 reported values reconcile; 0 mismatches; 0 unverifiable | IMPLEMENTED, CPU_TESTED, PASSED | [BACH_R4_POSTRUN_AUDIT.md](BACH_R4_POSTRUN_AUDIT.md) |
| P1 Task-discovery diagnosis | No implementation or schema defect; the coordinator's model-generated `priority="high"` over-filter in 15/15 trials; first appears with R4 | IMPLEMENTED, SCRIPTED_INTEGRATION_TESTED | [BACH_TASK_DISCOVERY_ROOT_CAUSE.md](BACH_TASK_DISCOVERY_ROOT_CAUSE.md) |
| P2 Minimal coordination repair | None justified (Decision B/C, stop conditions 1 and 8); T2 isolated test prepared | BLOCKED (PI) | [BACH_COORDINATION_REPAIR_IMPLEMENTATION.md](BACH_COORDINATION_REPAIR_IMPLEMENTATION.md) |
| P3 Specialist execution diagnosis | 45/45 assignments delivered; every specialist performs every role; 115 unauthorized attempts, no single provable cause | IMPLEMENTED, CPU_TESTED | [BACH_SPECIALIST_EXECUTION_ANALYSIS.md](BACH_SPECIALIST_EXECUTION_ANALYSIS.md) |
| P4 Legitimate workflow repair | No environment defect; scripted legal workflow completes with 0 checker matches | SCRIPTED_INTEGRATION_TESTED, PASSED | [BACH_LEGITIMATE_COMPLETION_ANALYSIS.md](BACH_LEGITIMATE_COMPLETION_ANALYSIS.md) |
| P5 Security/effect validation | 218/218 snapshot pairs verified; 0 unknown; controls 4/4 and 0 | SCRIPTED_INTEGRATION_TESTED, PASSED | [BACH_EFFECT_MEASUREMENT_VALIDATION.md](BACH_EFFECT_MEASUREMENT_VALIDATION.md) |
| P6 Manual Qwen tests and gates | T2/T3 ready (not run); B0-only smoke, calibration and concurrency BLOCKED; gates NO-GO | IMPLEMENTED, CPU_TESTED (stub); LIVE_QWEN_TESTED: none | [BACH_PHASE1_MANUAL_RUN_GUIDE.md](BACH_PHASE1_MANUAL_RUN_GUIDE.md), [BACH_PHASE1_FINAL_GATE_REPORT.md](BACH_PHASE1_FINAL_GATE_REPORT.md) |

## Verified root causes, by layer

| Layer | Finding |
|---|---|
| Tool schema | Correct and complete; forwarded unchanged by Qwen Code. Possible side-effect: the R4 publication of the `priority` enum coincides with a new `priority="high"` filter (0/5 before, 15/15 after). Causality is tested by T2. |
| Task-board filtering | Correct: exact-match AND filters; seeded `medium` is ClawTeam's default; empty filtered results on a non-empty board behave as documented. |
| Orchestration | The coordinator never directs specialists (0/45 received a message; 12 late sends). Specialists find their own work without it. |
| Model behaviour | Coordinator over-filters and loops or duplicates tasks. Specialists perform every role. The risk officer never lowers all three limits (0/15). Team-name copying errors (dropping "8B"). |
| Specialist execution | 95 unauthorized committed effects. Races cause 26/27 failed trader sells. |
| Completion measurement | Correct and unchanged. The legacy judge (0.8–0.9) disagrees with the deterministic core (0/15). Numeric suitability is UNKNOWN by design. |
| Unknown | Causal weight of the H3 schema side-effect vs H4 wording vs H5 capability (T2), and intrinsic vs interactive role crossing (T3). |

## Tests (recorded in `results/research_phase1c/tests/`)

| Test | Result |
|---|---|
| Full repository suite | 396 passed |
| `tests/test_phase1_completion.py` | 60 passed |
| `tests/test_research_phase1r4.py` | 53 passed (unchanged) |
| Ruff | pass |
| compileall `-W error` | pass |
| `bash -n` | pass |
| `git diff --check` | pass |

Preservation: 15,806 pre-edit files byte-identical (`verify-preservation`).

## Integrity notes

* All new code is in `multi_agent/research_phase1c/`, `scripts/research_phase1/phase1_completion.sh`, `scripts/research_phase1/validate_phase1_completion_bundle.py` and `tests/test_phase1_completion.py`. None of these are in a hashed target source set.
* While locating the R4 preflight logic, `phase1r4_manual.sh preflight` was run once. It rewrites `results/research_phase1r/target_v1r4/preflight.json`. That file was restored byte-identically from the R4 reviewer zip (sha256 `804e5012…`), and the new launcher's `identity-check` is read-only instead.

## Next

The decision document is [BACH_PHASE1_TO_PHASE2_DECISION.md](BACH_PHASE1_TO_PHASE2_DECISION.md). The PI's first decision is whether to approve T2/T3. Do not start Phase 2.
