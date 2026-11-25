# TARGET_V1R2 implementation and manual validation

Post-run update: the user's manual v3 smoke **failed**. Its packaging-validator
normalization bug is corrected separately with CPU postprocessing. See
`BACH_SMOKE_V3_REVIEW.md` for evidence, validation and the remaining blockers.
The release preparation and manual command below are historical; preserve v3 and
do not rerun or advance phases pending review. The frozen target is unchanged.

The approved change adds general trusted workflow guidance to the four role
system prompts for the allowlisted fin-t1 target. The original role instructions
remain intact. The earlier proposed appendix was not applied verbatim. No
additional benchmark answer, numeric limit, security-rule text or trade decision
is supplied by the new guidance.

## Investigation before prompt changes

`BACH_SMOKE_V2_ROOT_CAUSE.md` documents the CPU reconstruction. All 80 B1p hub
queries used the same compound owner filter and returned successful empty lists.
Five specialist task-state transitions were observable during those queries.
The trader received a valid assignment with both contract IDs, but attempted
no finance call; the hub sent no direction. Unsupported completion and failed
coordination are observed, without an observed finance-tool failure. The hub
then reached the native 80-turn limit. Context before a first finance call is
unknown for an agent that made none. The task's explicit request, catalog field
definitions and Bank behavior were reviewed before interpreting risk changes.
No generic finance rule or predetermined limit was added to the prompts.

## Prompt delivery and scientific boundary

`multi_agent/research_phase1r/prompt_repair.py` contains only general role and
workflow text. It asks the manager to coordinate existing assignments and
dependencies, inspect actual progress and summarize accurately. Specialists
evaluate actual risk settings, execute assigned authorized transactions, or
verify and document actual outcomes according to their roles. All receive
guidance on using assigned identifiers, registered tool names, tool-response
evidence, and reporting blockers rather than unsupported completion or loops.
The coordinator additionally receives the documented exact-owner filter semantics.

`RepairedRuntime.register_agent_instructions` adds this text only when explicitly
selected as TARGET_V1R2. Original builders, original task YAML, access rules,
permissions, FinanceBank, fixtures and checker are unchanged. User prompts and
seeded protected assignments retain the prior version's contents. The full tool
catalog remains exposed to all roles; there is no role-call enforcement, alias,
generated-call rewrite, scripted transaction or forced completion. The original
task context can mention task-specific details; the added guidance does not.
Only the original goal/role data feed prompt rendering; audit-rule and completion
rubric fields are never inputs to the added guidance.

The same system guidance is applied in B0, B1n and B1p. The requester notes,
schedule, seed derivation, checkpoint revision, temperature .7, top_p .8,
top_k 20, thinking off, context 32768 and original parser settings are unchanged.
One sequential trial per benign arm is planned. Global/per-trial request ceilings
are 1200/400, agent timeout 300 seconds, benchmark max_turns 30 and native
max_session_turns 80. These are the prior smoke settings. No smoke criterion or
deterministic checkpoint has been weakened. Full conservative numeric suitability
remains UNVERIFIABLE because the task supplies no exact numeric standard.

## Frozen release and prompt evidence

The CPU preparation command is:

```bash
bash scripts/research_phase1/phase1r.sh prepare-target \
  --results-dir results/research_phase1r/target_v1r2
```

The resulting `TARGET_V1R2.json` binds source hashes, original task/configuration,
checkpoint, CLI executable, effective system-prompt hashes, approved smoke limits
and orchestration metadata to a content digest. Changing a prompt changes that
identity. Preparation refuses to replace a different existing release. The real
smoke must match the frozen manifest before any model-server launch. Source
hashes are checked again before each wave.

`target_v1r2/prompt_diffs/` contains exact before/after UTF-8 files, unified diffs
and SHA256s for each role/condition. Canonical template examples use explicit
team/sentinel placeholders and are labeled accordingly. During the actual run,
each trial records exact system, user and assignment pairs using the real run's
IDs in `instrumentation/prompt_diffs/`; native `system_prompt.md` and
`instrumentation/visible_prompts/` record the delivered text. Only the system
pairs differ between V1R and V1R2. Original campaign identities and frozen source
trees remain unchanged; their results are not pooled with R2.

## Manual v3 smoke only

Run this single command from your terminal:

```bash
cd /home/ai5377/HarnessAudit && bash scripts/research_phase1/phase1r2_smoke_v3.sh
```

It selects GPUs 0–3 while respecting inherited CUDA/Slurm allocation restrictions,
uses the pinned locally cached Qwen3-8B, disables downloads/paid fallback and
requires the frozen release. It refuses any existing `diagnostic_smoke/v3`
directory, makes one invocation and never retries. The controller may stop the
remaining arms on an observation failure, as in previous smokes. The wrapper
does not run calibration, benchmarks, attacks, Phase 2 or Bayesian BACH.

Evidence includes native traces/actual tool responses, initial/final and per-call
SQLite snapshots, effect ledger, deterministic completion, request accounting,
session-limit receipts, assignment/context evidence, runtime convergence and
owned-process/GPU cleanup. `smoke_diagnosis.json` lists failed criteria, unstarted
trials, per-trial checkpoint evidence, known stop causes and cleanup. Each trial's
`task_board_review.json` reconstructs actual queries and returned task-state
observations. Missing observation stays unknown. A failure is preserved for
review; no prompt, model, sampling or ceiling is changed automatically.
`controller.log` captures the invocation's stdout/stderr, including startup
blocks before a campaign exists. `manual_launch_receipt.json` records its exit
code, campaign-start status, cleanup evidence (or NO_CAMPAIGN_STARTED) and zero
retries. The shell wrapper is CPU-tested against an isolated stub CLI for
startup block, failed-smoke and successful-smoke paths, including refusal of a
second invocation against the same directory.

The wrapper refreshes `results/research_phase1r/reviewer_bundle.tar.gz` after the
smoke, including a failed or partial run. Current packaging already includes the
previous V1/V1R evidence and the new implementation/CPU outputs. After extracting,
run `python validate_bundle.py .` using standard Python. Export privacy filters
redact runtime capabilities and omit private configs; original raw evidence and
its hashes remain in the repository. The validator checks exported hashes and
SQLite evidence; it does not claim an independent reimplementation of all rules.

No R2 real inference has been launched by the coding agent. A successful CPU
suite establishes implementation checks, not reliable model task completion.
Inspect the actual v3 result before authorizing any further experiment.

## Prepared release and executed CPU checks

Frozen identity:
`TARGET_V1R2:bc2c8f8481439c3723a9ba6cc739a46032d1cbb2d31e39e5131ed7031513c73d`.
The manifest is `results/research_phase1r/target_v1r2/TARGET_V1R2.json`;
`prompt_diffs/prompt_hashes.json` indexes the exact prompt pairs/diffs.

Full repository suite: **240 passed**, eight existing MCP SDK deprecation warnings,
34.93 seconds. The 18 R2 checks cover delivered role guidance and hashes,
task-context IDs, registered naming, identical condition treatment, audit/answer-key
non-injection, changed-prompt fingerprints, immutable releases, real task-board
filter semantics, all 80 actual v2 polls, stop evidence, frozen-manifest/limit
guards, blocked larger runs, failed-smoke diagnostics and exact fixture export.
They also exercise startup/failure/success logging and no-retry behavior using
an isolated CPU stub CLI.
Existing Phase 1R tests
also exercise live scripted MCP calls, effect verification and lifecycle cleanup.
Ruff, compilation with warnings treated as errors, shell syntax and whitespace
checks passed. Logs are under `target_v1r2/tests/`.

`preservation_verification.json` confirms all 2,104 protected files remain
byte-identical, covering the original pilot, smoke v1/v2 and original task,
checker, Bank, catalog, fixture and configuration files. All 145 frozen source
files match both the current implementation and their release copies. V3's
directory is absent at release preparation; no real R2 result is claimed.

Independent export comparison caught generic credential redaction changing four
ordinary financial `authorization` values in the copied synthetic records
fixture. The first, unrun frozen candidate is retained under
`target_v1r2_candidate_001`; it was not substituted into a real trial. Export now
preserves a fixture only when both its repository source-path suffix and exact
bytes match the known synthetic fixture. Other runtime authorization values still
pass through redaction, with a regression covering a disguised runtime file.
The final release above includes this export fix in its source identity.
Other intermediate, unrun freezes are retained as `target_v1r2_candidate_002`
and `target_v1r2_candidate_003`; the latter includes the initially failed wrapper
test log. The failure identified a missing parent-directory setup in an empty
checkout; the wrapper now creates the parent before atomically creating v3.
`target_v1r2_candidate_004` preserves the final pre-manifest-export-fix freeze.
The additional export check caught redaction of empty credential references and
source-path labels in the public manifest. The exporter now preserves a
self-consistent content-addressed R2 manifest only when its credential fields
are empty; a populated credential still goes through redaction. The regression
covers both cases. This makes the public fingerprint independently recomputable
without exempting runtime credentials from privacy filtering.
Only the final `target_v1r2/TARGET_V1R2.json` is selected by the manual command.
After bundle extraction, its additional exact-release check can be run with
`python phase1r/target_v1r2/tests/verify_release_export.py .`; this checks the
content-addressed fingerprint, frozen source bytes, all prompt pairs and prior
failure evidence in addition to the general validator.

The extracted release passed both validators: all 145 frozen source files and
33 exact prompt pairs matched; prior smoke v1/v2 evidence and SQLite snapshots
were present. Exported Phase 1R source also compiled with warnings treated as
errors. The receipts are `target_v1r2/tests/export_verification.json` and
`independent_bundle_validation.log`. No R2 model trial is present or claimed.
