# TARGET_V1R4 manual run guide

Everything runs from `/home/ai5377/HarnessAudit`. Only `gpu-preflight` (read-only GPU
inspection) and `smoke` touch GPUs, and only when you invoke them. Nothing retries,
raises a limit, changes the model, or advances to a larger study.

## Before the GPU run (CPU only)

```bash
cd /home/ai5377/HarnessAudit
bash scripts/research_phase1/phase1r4_manual.sh preflight           # identity, preservation, controls, caches
bash scripts/research_phase1/phase1r4_manual.sh test-tool-contract  # schema comparison + V1R4 tests
bash scripts/research_phase1/phase1r4_manual.sh audit-v4            # read-only v4 reconstruction
```

`preflight` must print `"ready_for_manual_smoke": true`. Do **not** commit or edit
tracked files (e.g. `README.md`) between now and the smoke: the frozen identity includes
the git HEAD and tracked diff, and the controller will refuse a mismatch. If that
happens, do not overwrite the release; ask for a new release decision.

## The v5 smoke (one command)

Check that nobody else is using GPUs 0–3 (`nvidia-smi`), and that no Slurm allocation or
`CUDA_VISIBLE_DEVICES` restriction excludes them, then run:

```bash
cd /home/ai5377/HarnessAudit && bash scripts/research_phase1/phase1r4_manual.sh gpu-preflight && bash scripts/research_phase1/phase1r4_manual.sh smoke
```

* `gpu-preflight` inspects GPUs 0–3, the pinned cached checkpoint and the Qwen Code CLI;
  it starts no server. The controller re-checks placement immediately before launch.
* `smoke` writes only to `results/research_phase1r/diagnostic_smoke/v5` (refuses if it
  exists), runs TARGET_V1R4 with one trial per benign condition in the randomized order
  B0-0, B1p-0, B1n-0, and stops at the first trial without valid measurement.
  Limits: 1,200 requests total / 400 per trial, 300 s agent timeout, 80 native turns,
  max-turns 30, seed 0, concurrency 1, four TP=1 replicas on GPUs 0–3, Qwen3-8B
  (pinned revision), temperature 0.7, thinking off, local only, no downloads, no paid
  fallback. `GPU_IDS` other than `0,1,2,3` is refused because it changes the frozen
  inference configuration.
* Afterwards it writes `manual_launch_receipt.json` (exit code, `retries: 0`) and
  builds the reviewer bundle automatically, including on failure. It never packages or
  rewrites the historical `results/research_phase1r` aggregates.

## The benign study: 5 trials each for B0, B1n and B1p (user-approved)

```bash
cd /home/ai5377/HarnessAudit && bash scripts/research_phase1/phase1r4_manual.sh gpu-preflight && bash scripts/research_phase1/phase1r4_manual.sh benign-study
```

* 15 real trials in 5 randomized blocks (each block has one B0, one B1n and one B1p).
  These are the same trial IDs, seeds and order as the first 15 trials of the planned
  45-trial study.
* **Every trial runs and is kept.** There is no early stop, no retry and no replacement.
  A failed or incomplete trial is recorded as such and the study continues. The run does
  not require a passing smoke.
* The per-trial limits are the smoke limits (400 requests, 300 s, 80 native turns,
  max-turns 30, concurrency 1, seed 0). The total request ceiling is 6,000 (15 × 400).
  The model and sampling are unchanged. Expect roughly 1–1.5 hours.
* Output goes to `results/research_phase1r/diagnostic_calibration/r4_5_per_condition_v1`
  (the command refuses if that folder already exists). The run writes
  `r4_study_summary.json` with per-trial and per-condition counts and explicit
  denominators: missing evidence is reported as unknown, never as zero. It then builds a
  validated reviewer zip.
* n = 5 per condition gives descriptive engineering counts. Differences between
  conditions are not estimable effects at this size.

The smoke and the benign study are independent. You can run either or both, in either
order, and each writes to its own folder.

## Reading the result

| File | Meaning |
|---|---|
| `v5/smoke_acceptance.json` | Unchanged criteria: real execution, all planned trials started, observation complete, specialist context, B0 deterministic core, local model requests, cleanup |
| `v5/smoke_diagnosis.json` | Failed criteria, unstarted trials, per-trial checkpoints and convergence |
| `v5/trials/B0-0/instrumentation/coordination_tool_registry.json` | Schema actually served (hash must equal TARGET_V1R4.json) |
| `v5/derived/B0-0/coordinator_call_ledger.json` | First coordinator call, filters, errors, sends, stop reason |
| `v5/derived/B0-0/coordination_progress.json` | Observation-only loop diagnostic per role |
| `v5/derived/B0-0/effect_attribution.json` | Rule-matched attempts vs verified committed effects |
| `v5/campaign.json`, `gpu_after_cleanup.json` | Attempts, cleanup and GPU release |

If B0 fails and the smoke stops, that failure is the result: do not re-run B0 or reuse
`v5`. Then export (already done by `smoke`; repeatable on CPU):

```bash
bash scripts/research_phase1/phase1r4_manual.sh analyze   # re-derive v5 reports (CPU)
bash scripts/research_phase1/phase1r4_manual.sh package   # new validated reviewer zip
```

The zip is `results/chatgpt_handoffs/r4-<timestamp>-<suffix>/CHATGPT_R4_REVIEW.zip`,
with `validation_receipt.json` beside it. Its `validate_bundle.py` re-checks it with the
Python standard library only.

## Not authorized by this guide

The 45-trial (15 × B0/B1n/B1p) benign diagnostic, concurrency-2/4 benchmarks, B2/B3,
Phase 2 and Bayesian BACH. The controller refuses any TARGET_V1R4 command other than
`smoke` and `benign-study`.
