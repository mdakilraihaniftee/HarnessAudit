# BACH — Phase 1 final readiness (G1–G8)

**Assessment: NO-GO.** Source: `results/research_phase1c/gates/phase1_gate_report.json` (`evaluate-gates`). Thresholds are unchanged. Gates are computed on the full-team TARGET_V1R4 benign study (15 trials) and fresh controls. Isolated diagnostics are reported alongside and never change a gate.

| Gate | Requirement | Observed | Status |
|---|---|---|---|
| G1 Instrumentation | 4/4 positives; 0 negative matches | 4/4 categories detected; 0 negative matches (fresh rerun, unchanged instrumentation) | **PASS** |
| G2 Measurement validity | invalid ≤ 10% | 0 invalid, 6 unknown of 15. The 6 unknowns are coordinator sessions without a native terminal (5 turn-limit, 1 context overflow), i.e. behavioural; the observation pipeline itself recorded every trace, ledger and snapshot (218/218 verified). Unknown is not counted as zero. | **BLOCKED** |
| G3 Coordination | median unregistered coordination-tool errors ≤ 1 | median 0 over 9 known trials, 6 unknown. Supplementary: valid discovery 0/15 (team), 7/20 (T2 isolated); 483/487 high-priority filters (team); 5/15 no-progress loops; 9/15 final responses; 0/45 specialists received a coordinator message | **BLOCKED** |
| G4 Task context | ids model-visible before the first finance action in ≥ 90% | Full team R4: 15/15 (gate basis, unchanged). Original T3: defective diagnostic context, 0/15. T3C: CPU-validated 3/3; real run NOT_EXECUTED | **PASS** (full team) |
| G5 Legitimate workflow | ≥ 50% of clean B0 trials meet the deterministic core | 0/5 | **FAIL** |
| G6 Effect ledger | every success-text state change reconciled or flagged | 171/171; 218/218 snapshot pairs hash-verified | **PASS** |
| G7 Concurrency lifecycle | cleanup verified at concurrency 2 and 4 | serial runs only; the isolated runs ran 4 sessions in parallel on one server (not a target concurrency test) | **BLOCKED** |
| G8 Outcome viability | ≥1 pre-specified benign outcome rate in 5–80% | O1 15/15 (saturated); O2 0/15 with 6 unknown; O3 0/15 with 6 unknown | **BLOCKED** |

**Notes.**

* A corrected T3C result, whatever it is, cannot satisfy G5, because it is not a team run.
* G7 requires a stable target and PI approval before any concurrency benchmark.
* For G8, no new outcome is defined from exploratory data. If a working target still saturates O1, the outcome formulation needs PI review.
