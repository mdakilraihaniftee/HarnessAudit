# Phase 1R validation

Latest approved revision: TARGET_V1R2. Full suite **240 passed**, eight existing
SDK deprecation warnings, 34.93 seconds. Eighteen additional R2 tests include
actual v2 reconstruction, general prompt guidance, exact hashes/condition
invariance, audit-field non-injection, frozen launch/failure diagnostics and
exact synthetic fixture export with runtime credential redaction retained.
The manual wrapper's startup block, failed-smoke and success paths are checked
using an isolated CPU stub, including refusal to overwrite or retry a directory.
All 2,104 historical/protected files and 145 frozen source files passed
preservation checks. See `BACH_TARGET_V1R2_IMPLEMENTATION.md` and
`results/research_phase1r/target_v1r2/tests/`. No R2 GPU inference was launched.

During the initial implementation, no GPU inference, Slurm submission, model
download, attack confirmation or paid inference was performed. Control actions used isolated seeded
synthetic FinanceBank and ClawTeam MCP. Read-only GPU preflight is separate.

## Manual smoke follow-up, November 22, 2026

The user's first real smoke failed and is preserved at `diagnostic_smoke/v1`.
The versioned adapter now waits beyond task-board completion for native session
convergence before collection and Bank shutdown, within the original timeout.
Regressions cover a delayed specialist terminal, timeout, process exit without a
terminal and reused numeric PIDs. Prerequisite errors distinguish missing smoke,
failed criteria and changed target identity. Full CPU suite: **219 passed**, eight
existing MCP SDK deprecation warnings, 33.28 seconds. Log:
`results/research_phase1r/tests/smoke_lifecycle_fix.log`. Ruff, compilation, shell
syntax and whitespace checks passed. The coding agent launched no GPU rerun;
the fix and legitimate model completion still need a new versioned manual smoke.

The user's subsequent v2 smoke captured all B0 terminals but still failed core
completion; B1p explicitly hit Qwen's 80-turn session limit. The CPU follow-up
now recognizes that exact CLI receipt, hashes its source and records the affected
role and observed assistant-message count. Missing terminals remain incomplete
measurement; infrastructure failures retain precedence. No policies, ceilings,
prompts or models were changed. New regressions verify explicit turn-limit
evidence, a non-limit warning and concurrent MCP initialization failure. Full
suite: **222 passed**, eight existing SDK deprecation warnings, 32.84 seconds;
`results/research_phase1r/tests/smoke_v2_diagnosis.log`. The separate read-only
review is `results/research_phase1r/smoke_v2_cpu_review.json`. Raw v2 files remain
unchanged. The next prompt intervention is proposed for approval, not applied.

## Executed evidence

- Original-pilot CPU reconstruction: essential claims reconciled, A1–A13 tables
  produced, original artifact hashes checked. Command:
  `bash scripts/research_phase1/phase1r.sh audit-original`.
- Offline controls: all four intended categories; clean negative has zero matches.
  `bash scripts/research_phase1/phase1r.sh test-instrumentation`.
- Scripted live integration: real loopback finance MCP and actual ClawTeam stdio
  MCP mailbox calls; captured raw calls/results passed through the original Qwen
  ingestor and checker. Four positive categories, clean zero matches, verified
  unauthorized trade change, existing designated resource and recipient mailbox
  retrieval evidence. The legitimate script passes deterministic core completion.
  `bash scripts/research_phase1/phase1r.sh test-scripted-harness`.
- Final full regression: 212 tests passed in 33.77 seconds.
  The suite includes archive/unknown-outcome and concurrency/partial-startup checks;
  see the final log/receipt in `results/research_phase1r/tests/` for its actual result.
- Ruff, Python compilation with SyntaxWarnings treated as errors, bash syntax
  and git whitespace checks are captured in the same tests directory.
- Independent standard-library bundle validator checks exported hashes, JSONL,
  statistical arithmetic/denominators and SQLite row/difference/trade evidence.
  `bundle_validation.json` records the exact export check. Full semantic policy
  replay additionally requires the repository dependencies; the independent
  validator does not claim to reimplement every checker rule.

## Failures encountered and corrected

The first sandboxed live control could not open sockets. It is recorded as an
infrastructure failure, not a model failure. Loopback CPU tests were then run
with the required socket permission. An initial MCP readiness race appeared
between printing the port and accepting connections; explicit bounded readiness
polling fixed it before final controls. An early negative-control setup raised a
position limit instead of lowering it and used the fixture's wrong sector spelling;
its completion failure was preserved, and the legitimate scripted workflow and
state-derived tests corrected the setup. An initial archive formatting bug was
caught by compilation/export, fixed, and covered by a round-trip archive test.
Earlier failed control directories are preserved, not substituted into a pass.

## Limits of validation

The native model's use of corrected examples/context, runtime seed honoring,
legitimate completion rate, numerical Conservative suitability, concurrent vLLM
behavior, live GPU resource release and throughput remain untested. CPU concurrent
process fixtures do not satisfy G7. Source identities explicitly disclose missing
historical components. Every real diagnostic command is manual and requires
RUN_REAL=YES; a fresh versioned directory avoids replacing a consumed attempt.

A separately extracted candidate export passed the stdlib validator (1,979 files)
and reran A1–A13 using the exported source from outside the repository: exit 0,
RECONCILED, no essential artifact gaps. The release export is validated again
while packaging. Receipts are in tests/export_verification.json and
tests/independent_export_validation.log.

The owned-child cleanup fixture was also rerun with the child in a separate
process session: one passed; see tests/separate_session_child.log.

Final export-specific regression checks: three passed, including preservation
of immutable ClawTeam prompt source and reliability-aware concurrency summaries.
