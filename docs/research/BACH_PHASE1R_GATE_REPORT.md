# Phase 1R gate report

Current authorization: prepare a separate TARGET_V1R3 repair, run a manual v4
smoke, then 15 benign trials per condition only if the same target passes smoke.
R3 preparation passes 283 repository tests; no real R3 execution or passing smoke
is established. Concurrency and later phases remain on hold. See
`BACH_TARGET_V1R3_IMPLEMENTATION.md`. Earlier checkpoints below are historical.

The user ran TARGET_V1R2 smoke v3: **FAIL**. The hub timed out, observation
was incomplete and the core risk update failed. The separate packaging-validator
bug is corrected with CPU postprocessing; 253 repository tests pass. The frozen
release and failed raw result remain unchanged. Calibration, concurrency and
later phases remain on hold pending review; no rerun is authorized by this fix.
See `BACH_SMOKE_V3_REVIEW.md` and `BACH_TARGET_V1R2_IMPLEMENTATION.md`.

Engineering readiness is **BLOCKED / NOT YET TESTED** for Phase 2. Passing CPU
controls does not establish reliable real-Qwen execution. Machine evidence:
`results/research_phase1r/gate_status.json`. Thresholds are the PI's proposed
engineering gates and have not been lowered.

Manual smoke update (November 22, 2026): `diagnostic_smoke/v1` is **FAIL**.
Only B0-0 started, native observation was incomplete and core completion failed.
Calibration was correctly blocked before any target trial started. Concurrency
2/4 remain blocked. The early-collection repair requires a fresh versioned smoke;
it does not change the failed attempt. See `BACH_CURRENT_FINDINGS.md` and the
recovery commands in `scripts/research_phase1/PHASE1R.md`. The table below records
the initial implementation checkpoint before this manual smoke.

The subsequent `diagnostic_smoke/v2` also failed: B0 has complete observation
but failed core completion; B1p's hub explicitly exhausted its 80-turn session
limit while repeating task queries. Its three terminals leave observation
incomplete. Both owned cleanup and GPU release passed. Concurrency remains
blocked; the next proposed intervention is a separately approved target prompt
revision in `BACH_TARGET_REPAIR_PROPOSAL.md`, rather than another identical smoke.

| Gate | Current status | Evidence and next step |
|---|---|---|
| G1 instrumentation | PASS | Four categories detected in offline replay and real scripted MCP integration; both negative controls have zero matches |
| G2 measurement invalidity <=10% | NOT_EVALUATED | No repaired-target real trials; run benign calibration and retain unknowns |
| G3 median unregistered coordination errors <=1 | NOT_EVALUATED | No repaired-target native model trajectories; report full distribution after diagnostics |
| G4 IDs before specialist finance in >=90% trials | NOT_EVALUATED | Storage/prompt unit tests pass; real preceding model-visible results still required |
| G5 >=50% B0 deterministic core completion | NOT_EVALUATED | Scripted legitimate workflow passes core, but it is not Qwen evidence |
| G6 all success responses accounted for | NOT_EVALUATED for real diagnostic | CPU snapshot/effect tests pass; independently reconcile every native success in manual trials |
| G7 cleanup at concurrency 2 and 4 | BLOCKED | Controlled CPU lifecycle fixtures pass; both real GPU configurations remain untested |
| G8 viable prespecified benign outcome 5–80% | NOT_EVALUATED | No repaired-target benign rates. O1/O2/O3 reported without selecting a favorable endpoint |

Resource preflight is read-only: GPUs 0–3 were visible and idle with approximately
44.47 GiB free each at the recorded check. The placement plan accepted the pinned
cached model. Availability is transient and must be checked before a manual run.
Historical PID 3202726 was absent in the host process check; no process was signaled
based on historical PID. These facts do not establish real serving, throughput,
request seeding behavior, MCP model use or GPU cleanup.

## PI decisions (unresolved)

| Decision | Evidence | Options and implications |
|---|---|---|
| D1 role instructions | Actual system-prompt excerpts are in prompt_awareness.json. Coordinator already names specialist ownership; specialists mainly know their own scope. Existing harness says hub is pure coordinator. | Retain existing prose for a minimal repair comparison, or separately version explicit cross-role ownership instructions. The latter changes target behavior/security awareness and needs approval. |
| D2 target model | Qwen3-8B historical legitimate behavior is unreliable; repaired execution is not yet measured. | Retain the pinned model for diagnostics. Compare a stronger open-weight model only after legitimate workflow evidence justifies cost; no model was changed/downloaded. |
| D3 aliases | 103 unregistered native errors, conflicting bare examples, and correctly prefixed wrong-team calls. Existing system note already documents prefixes. | Correct examples (implemented), approve exact narrow aliases in a separate version, or revise CLI/tool presentation. Aliases cannot fix wrong teams or missing business context. None were implemented. |
| D4 primary outcome and B3 estimand | Historical V-OR events all used nonexistent resources; valid-only broad violation outcome saturated. No repaired benign outcome rates yet. | Decide among predeclared O1/O2/O3 after calibration; do not select by attacker benefit. Frozen note set estimates its declared distribution, frozen stochastic generator estimates the locked sampling law. Neither is automatically chosen. |
| D5 original/repaired comparison | Historical traces/frozen source preserved; missing historical adapter/vendor bytes explicitly listed. New source and configuration are versioned. | Include both as separate targets with a reproducibility caveat, or use old pilot as a diagnostic motivating case. Do not pool them; a matched prospective rerun would be a distinct approved study. |

The exact numeric definition of Conservative-appropriate limits also requires
PI interpretation: the original task supplies no thresholds. The operational
core and full-completion unknown are reported separately.
