# Phase 1: supervised 15-target launcher and reviewer bundle

Latest Phase 1R checkpoint: the original launcher report below is historical.
After failed R2 smoke v3, the user selected a separate R3 repair followed by
15 trials per B0/B1n/B1p (45 total), gated on fresh smoke v4 with the same identity.
See `BACH_TARGET_V1R3_IMPLEMENTATION.md` for current manual commands and evidence.
No R3 GPU run has been launched by the coding agent.

Prepared 2026-11-22 (America/New_York) in the existing modified HarnessAudit
worktree. This extends the fixed Phase 1 run interface. It does not change the
benchmark task, policies, SI-ATG architecture, frozen note conditions, original
checker or research classification, and implements no Phase 2 discovery.

## Exact manual command and upload file

```bash
cd /home/ai5377/HarnessAudit
GPU_IDS=0,1,2,3 RUN_REAL=YES \
  MODEL_ID=Qwen/Qwen3-8B REPEATS=5 MAX_TARGET_RUNS=15 \
  MAX_PARALLEL_TRIALS=4 MODEL_REQUEST_BUDGET=6000 \
  MODEL_REQUESTS_PER_TRIAL=400 SKIP_JUDGE=0 \
  OUTPUT_DIR=/home/ai5377/HarnessAudit/results/research_phase1/pilot_15_real \
  bash scripts/research_phase1/08_run_15_real_gpu_pilot.sh
```

The single upload file produced by that command is:

```text
/home/ai5377/HarnessAudit/results/research_phase1/pilot_15_real/reviewer_bundle.tar.gz
```

Upload it even if some runs fail; those failures are part of the evidence.
Check `run_status.json` inside first. A prepared command is not evidence that
fifteen real targets have completed.

## GPU and trial strategy

The existing `docs/LOCAL_PERFORMANCE_REPORT.md` measured Qwen3-8B on four L40S
GPUs. At concurrency 16, TP=4 delivered about 354 aggregate output tokens/s,
whereas DP=4 delivered about 600; DP=4 reached 1,066 at concurrency 32. Single
request latency differs, and this is historical serving evidence rather than a
new end-to-end speedup measurement.

Accordingly, launcher 08 chooses one vLLM service with four TP=1 data-parallel
replicas, one copy of the cached weights on each requested GPU. It runs up to
four independent target processes. Each process reuses the existing
`run_trials -> build_run_context -> BenchmarkRunner -> ClawTeam -> Qwen Code
-> Finance MCP -> FinanceBank` path. Per-process Python globals, task banks,
unique team/run IDs, workspaces and native output directories isolate trials.
The target-model configuration and local judge settings are identical across
conditions. The first B0 is a standalone native MCP smoke and is included in
five B0, five B1 and five B2 targets, not added as a sixteenth.

The trial order remains fixed and seeded, with the first B0 as an infrastructure
gate. B1/B2 variants cycle the same three existing fixtures, recorded by note ID.
Concurrent shared serving changes scheduling and batching; that fact is recorded
and limits independent/matched-seed interpretation. No generated candidates,
learning, selection bias correction or adaptive objective is introduced.

GPUs are inspected immediately before serving. Inherited CUDA masks and Slurm
allocation restrictions are intersected with physical IDs, using the existing
strict Phase 1 mapping checks. Launching refuses occupied GPUs, even unrelated
workloads under the same UID. It never widens allocations, selects another GPU,
kills someone else's process, assumes idle means allocated, or enables sharing.
Reuse requires a locally recorded owned endpoint with matching cached checkpoint,
actual GPU worker ancestry/UUID mapping, four-replica TP=1 layout, context,
sequence limit, server sampling and thinking configuration. Unknown placement
is rejected. Reused servers are not cleaned up by this invocation.

## Budgets and lifecycle

- Default logical target budget: exactly 15 slots, with 5 per condition.
- Model budget: at most 6,000 forwarded model request attempts total, and 400
  per target across its agents, local judges and retries. No hard token-total
  promise is made; generation/context/turn settings remain recorded.
- A loopback-only streaming gateway reserves each POST before dispatch. Failed
  or interrupted requests consume budget; counters persist across resume. It
  refuses nonlocal upstreams, external redirects and unsupported routes. No
  paid, cloud, download or mock fallback exists.
- The model and inference routes are explicitly local. Qwen Code credentials
  and hosted defaults are scrubbed by the existing runtime and worker setup.
  Native network logs and proxy counts provide additional inspectable evidence.
- Each target is a fresh subprocess, rather than parallel threads around mutable
  environment/configuration globals. The per-worker helper accepts only a note
  offset and a separately verified upstream endpoint for the supervised proxy.
- A root lock spans execution, shutdown and packaging. Attempt slots are
  reserved before workers start. Repeating the same command never duplicates a
  completed target and does not retry consumed failed/interrupted slots.
  Unreserved slots continue. A still-live prior worker causes a safe refusal.
- Source/configuration/checkpoint changes require a new output directory. Sources
  are snapshotted, and a source change during a run stops new reservations.
- Completed reservations are analyzed/repackaged without allocating GPUs.
  Failures are retained; replacements require a new, explicit budget and reason.
- SIGINT/SIGTERM triggers supervised worker cleanup and stops only an owned
  server started by this invocation. An uncatchable kill can leave a server;
  subsequent reuse must verify it. The script never broadly kills node PIDs.
- Exit status distinguishes completion from a partial/blocked campaign. The
  report separately counts reserved slots, actual targets started, actual
  targets completed, valid research traces, and legitimate-task completion.

## Bundle contents and independent checks

The atomic `reviewer_bundle.tar.gz` contains:

- `README.md`, `EXPORT_MANIFEST.json`, and `validate_bundle.py`;
- Git/source/configuration snapshots and requester notes;
- original per-slot normalized JSONL and native Qwen traces, original checker
  results, protected effective tasks, judge observations and execution logs;
- trial/rule JSON and CSV datasets, forbidden/unnecessary distinctions, task
  completion and judge failures, action/communication and error summaries;
- B0/B1/B2 comparisons, Wilson intervals and Newcombe-Wilson difference intervals;
- model request counts/timing/status, GPU samples, vLLM metrics, server launch,
  actual placement, cleanup outcomes and test results.

Unadjusted intervals are descriptive and assume independent Bernoulli
observations. Five samples per condition, fixed-note mixtures, shared batching,
server seeds and missing/invalid traces limit that assumption. No causal,
certified-attack or publication-quality inference is claimed.

Only sanitized review copies are exported; local original evidence is preserved.
Credentials from environment values, common secret formats, home/repository
paths and host names are redacted. MCP configuration, settings, weights,
checkpoints, SQLite bank snapshots, symlinks, control specs and unrelated data
are excluded. Original evidence hashes describe original bytes; the export
manifest describes the sanitized bytes actually present in the archive.

After extracting, run:

```bash
python validate_bundle.py .
```

It uses only Python's standard library on CPU: it verifies exported checksums,
trial/rule totals, saved original non-disclosure tool/resource/channel matches,
and recomputes all descriptive statistics and intervals. V-ID recognizer
matching and realized effects are explicitly outside this independent check.
The original decisions, authoritative task and relevant implementation remain
available for inspection.

## Verification actually performed

The implementation resides in:

- `scripts/research_phase1/08_run_15_real_gpu_pilot.sh`;
- `multi_agent/research_phase1/pilot.py`: supervision, preflight, scheduling,
  server ownership, cleanup and resume;
- `request_proxy.py`: persistent model-request budget and streaming gateway;
- `cohort_bundle.py`: immutable child-trial collection, reports and export;
- `review_bundle.py`: portable validation and descriptive uncertainty;
- `experiment.py`: additive note offset, proxy/upstream verification and server
  version provenance; original sequential interfaces are preserved;
- `tests/test_research_launcher.py`: budget, HTTP, isolation, archive, occupied
  GPU refusal, verified reuse, ledger, note offset and uncertainty tests.

PASS: full 15-slot mock campaign with four worker processes: 15 valid mock
trajectories, 585 rule observations, zero real executions. Repeating the command
kept the same fifteen reservations, launched no duplicate targets or server,
and rebuilt the bundle. The exported CPU validator returned PASS for all 15
trial rows, 585 rule rows and exported-file checksums. Corrupt bundle tests
fail rather than silently accepting modified evidence. Model-request budget
thread/concurrency, streaming, redirect refusal and persistence tests passed.

PASS: **158 full regression tests**; **72 targeted Phase 1/launcher tests**.
PASS: Ruff, Python compilation, Bash syntax and diff whitespace checks.
The exact recommended environment (including MAX_PARALLEL_TRIALS=4) was
exercised with explicit mock targets, and a second invocation reserved no new
slots. Exported JSONL stays one record per line. Before publishing an archive,
the launcher independently validates its sanitized evidence and includes
`tests/bundle_validation.json` as a receipt.
The complete regression result is recorded in
`results/research_phase1/launcher_verification/full_final.log`.
Early development failures (a source edit during strict resume validation, and
missing imports in a newly added test) were diagnosed and corrected. Historical
experiment artifacts and the original Phase 1 results were not changed.

### Real smoke attempt: BLOCKED before target execution

Initial host inspection found all four L40S GPUs idle, approximately 45.5 GiB
free each, without compute processes. The installed stack and cached checkpoint
were verified: Qwen Code 0.27.4, vLLM 0.33.0, Qwen3-8B revision
`b968826d9c46dd6066d109eabc6255188de91218`.

By the authorized smoke's immediate preflight, new unrelated Python workloads
were using all four GPUs (roughly 17 GiB per GPU; several GPUs actively computing).
The launcher safely refused startup with `GPU 0 is occupied ... no automatic
sharing`. It started no model server, executed no target, consumed no model
requests, and did not touch those processes. This is a real preflight refusal,
not a model/tool-calling success or a mocked replacement.

The blocked attempt's evidence is preserved separately at:

```text
results/research_phase1/launcher_real_smoke/reviewer_bundle.tar.gz
```

It explicitly reports zero reserved/started/completed real targets. This
validation bundle is not the user's future 15-target result bundle. The latter
will be created at `pilot_15_real/reviewer_bundle.tar.gz` by the command above.
No claim is made that fifteen real trials or fresh real ClawTeam finance tool
calls succeeded during this work. Live Slurm deployment, four-GPU serving under
this new supervisor, real proxy/model integration, target concurrency and
mid-inference cleanup remain unverified until the authorized GPUs are free.

The CPU/mock foundation and safety refusal paths are verified. Run the one
command when all requested resources are available, then upload its single
reviewer bundle for independent analysis before designing Phase 2.
