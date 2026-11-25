# Phase 1R manual runbook

Current sequence: **TARGET_V1R3 smoke v4, then 15 trials per benign condition
only after that exact target passes smoke**. See
`docs/research/BACH_TARGET_V1R3_IMPLEMENTATION.md`. Commands are separate:

```bash
cd /home/ai5377/HarnessAudit
bash scripts/research_phase1/phase1r3_manual.sh smoke
# Run the next command only after inspecting a passing v4 smoke:
bash scripts/research_phase1/phase1r3_manual.sh diagnostic
# CPU export works after either successful or failed runs:
bash scripts/research_phase1/phase1r3_manual.sh export
```

The diagnostic schedules 15 each for B0/B1n/B1p (45 total), sequentially, without
replacing failed or incomplete trials. No later phase or retry starts automatically.
The earlier checkpoints and V1R/R2 commands below remain history.

The user's **TARGET_V1R2 smoke v3 failed**. Preserve its evidence and do not
rerun or advance phases pending review. See `docs/research/BACH_SMOKE_V3_REVIEW.md`.

To prepare the existing evidence for a next-action review, run this CPU-only
export. It creates a fresh directory under `results/chatgpt_handoffs`, validates
the exported evidence and frozen release, and produces `CHATGPT_REVIEW.zip`,
`RESULTS_SUMMARY.md` and `NEXT_ACTION_REQUEST.txt`. Attach the ZIP and paste the
request text into ChatGPT. No model or experiment is launched and no tests are
rerun; the previous test receipts are included.

```bash
cd /home/ai5377/HarnessAudit
.venv/bin/python scripts/research_phase1/prepare_chatgpt_v3_review.py
```

The following command records the already completed manual invocation:

```bash
cd /home/ai5377/HarnessAudit && bash scripts/research_phase1/phase1r2_smoke_v3.sh
```

The prepared frozen release is in `results/research_phase1r/target_v1r2`.
The wrapper pins the original smoke limits and refuses an existing v3 directory.
It does not launch later phases or retry. The commands below retain the earlier
V1R procedure as history; calibration and concurrency remain on hold until v3 is
reviewed. R2's CLI explicitly blocks diagnostic/benchmark commands at this stage.

Run from `/home/ai5377/HarnessAudit`. CPU commands do not contact a model.
No GPU workload is launched during implementation. New campaign directories
must be versioned; reserved attempts are never silently replaced.

```bash
cd /home/ai5377/HarnessAudit
bash scripts/research_phase1/phase1r.sh preflight
bash scripts/research_phase1/phase1r.sh audit-original
bash scripts/research_phase1/phase1r.sh test-instrumentation
bash scripts/research_phase1/phase1r.sh test-scripted-harness
```

Controls append isolated runs and publish their latest receipt. Offline and live
positive controls must both pass before real launch. Read `gate_status.json` and
`docs/research/BACH_CURRENT_FINDINGS.md`. No aliases or new policy prose are added.

Inspect your allocation and GPU occupants, then run read-only resource preflight:

```bash
GPU_IDS=0,1,2,3 bash scripts/research_phase1/phase1r.sh gpu-preflight \
  --results-dir results/research_phase1r/resource_preflight
```

Physical IDs must be permitted by CUDA_VISIBLE_DEVICES and Slurm allocation when
present. Unknown remapping is refused. Same-UID compute jobs are not automatically
shared or killed. The pinned cached Qwen3-8B revision is required; downloads and
paid fallback are disabled. A reused server must pass exact checkpoint, sampling,
GPU UUID and layout compatibility checks. It remains running after the campaign.
A new server uses TP=1 with one replica per selected GPU, 32K context, max sequences
16, temperature .7, top_p .8/top_k20 and thinking off. Query actual resource
availability again immediately before manual launch.

Small three-arm smoke, one trial per arm, sequential:

```bash
GPU_IDS=0,1,2,3 RUN_REAL=YES REPEATS=1 MASTER_SEED=0 \
MAX_PARALLEL_TRIALS=1 MODEL_REQUEST_BUDGET=1200 MODEL_REQUESTS_PER_TRIAL=400 \
OUTPUT_DIR=results/research_phase1r/diagnostic_smoke/v1 \
bash scripts/research_phase1/phase1r.sh smoke
```

The smoke stops after any failed observation wave. Inspect the generated bundle,
ledger, native traces and lifecycle before authorizing the larger run yourself.
`smoke_acceptance.json` must pass before calibration or benchmarks. It requires
complete observations, specialist context, a clean core completion, model requests
and cleanup. Larger runs require the same target identity; use
`--smoke-results-dir` if the smoke uses another versioned directory. This is not
a final Phase 2 readiness decision.

The command prints `Smoke PASS` or `Smoke FAIL` and exits nonzero on failure.
A subsequent `ValueError: Smoke failed` is an intentional prerequisite block;
do not proceed to calibration or either concurrency benchmark. Task-board
completion alone is insufficient: the versioned adapter waits for every native
session to terminate (or its observed process to exit) within the existing agent
timeout before collecting traces and shutting down the Bank. The observation is
saved in each trial's `instrumentation/runtime_convergence.json`. Missing native
terminal events remain missing; the repair does not manufacture successful runs.

The first manual smoke at `diagnostic_smoke/v1` failed with a truncated
risk-officer session and incomplete core task state. Preserve that attempt. After
the adapter fix, the following v2 command was run manually and also failed
(workflow failure and an explicit hub session-turn limit). Preserve both
attempts. This command is retained as provenance, **not a recommendation to rerun**:

```bash
GPU_IDS=0,1,2,3 RUN_REAL=YES REPEATS=1 MASTER_SEED=0 \
MAX_PARALLEL_TRIALS=1 MODEL_REQUEST_BUDGET=1200 MODEL_REQUESTS_PER_TRIAL=400 \
OUTPUT_DIR=results/research_phase1r/diagnostic_smoke/v2 \
bash scripts/research_phase1/phase1r.sh smoke
```

Only after this prints `Smoke PASS`, add
`--smoke-results-dir results/research_phase1r/diagnostic_smoke/v2` to each
`diagnostic` and `benchmark` command below. A changed target identity requires
another fresh smoke. If the new smoke completes observation but fails core
completion, inspect the deterministic checkpoints and native actions before
changing target policy, model or sampling.

Current next step: review `docs/research/BACH_TARGET_REPAIR_PROPOSAL.md` before
another GPU run. The v2 wait fix captured all B0 terminals; B1p's hub exhausted
80 native session turns while repeatedly querying the board. This is distinct
from a 300-second agent timeout or the gateway request ceiling. Explicit CLI
stop receipts are recorded in `native_session_limits`; a missing native terminal
still makes observation incomplete. The benchmark `MAX_TURNS` setting does not
override Qwen's separately recorded `max_session_turns`; neither ceiling was
raised during diagnosis. No concurrency campaign is authorized by a failed smoke.

Optional engineering calibration, configurable ten trials per benign arm:

```bash
GPU_IDS=0,1,2,3 RUN_REAL=YES REPEATS=10 MASTER_SEED=0 \
MAX_PARALLEL_TRIALS=2 MODEL_REQUEST_BUDGET=12000 MODEL_REQUESTS_PER_TRIAL=400 \
OUTPUT_DIR=results/research_phase1r/diagnostic_calibration/v1 \
bash scripts/research_phase1/phase1r.sh diagnostic
```

Independent comparable concurrency diagnostics, two repeats per benign arm:

```bash
GPU_IDS=0,1,2,3 RUN_REAL=YES REPEATS=2 MASTER_SEED=0 \
MAX_PARALLEL_TRIALS=2 MODEL_REQUEST_BUDGET=2400 MODEL_REQUESTS_PER_TRIAL=400 \
OUTPUT_DIR=results/research_phase1r/concurrency_benchmark/2 \
bash scripts/research_phase1/phase1r.sh benchmark
```

```bash
GPU_IDS=0,1,2,3 RUN_REAL=YES REPEATS=2 MASTER_SEED=0 \
MAX_PARALLEL_TRIALS=4 MODEL_REQUEST_BUDGET=2400 MODEL_REQUESTS_PER_TRIAL=400 \
OUTPUT_DIR=results/research_phase1r/concurrency_benchmark/4 \
bash scripts/research_phase1/phase1r.sh benchmark
```

Run these separately only when the allocation is available. Samples and budgets
are configurable; there is no fixed maximum trial count. Four GPUs do not imply
that concurrency four is better. The report exposes validity and cleanup beside
throughput. Internal replica selection is not falsely reported as observed.

CPU-only analysis/export, after a calibration has been run:

```bash
bash scripts/research_phase1/phase1r.sh analyze \
  --results-dir results/research_phase1r/diagnostic_calibration/v1
bash scripts/research_phase1/phase1r.sh evaluate-gates \
  --results-dir results/research_phase1r/diagnostic_calibration/v1
bash scripts/research_phase1/phase1r.sh evaluate-gates \
  --results-dir results/research_phase1r
bash scripts/research_phase1/phase1r.sh package \
  --results-dir results/research_phase1r
```

The root archive includes nested smoke, calibration and both benchmarks when
present. Root gates record no pooled target trials: evaluate each campaign's
G2–G6/G8 separately; root G7 reads the two separate benchmark manifests. No
historical/new-target pooling occurs. Campaigns package their own results too.
The upload archive is `results/research_phase1r/reviewer_bundle.tar.gz`.
After extraction, run `python validate_bundle.py .` with standard Python.
A full semantic replay additionally requires the copied repository dependencies.

Full CPU regression verification:

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check multi_agent/research_phase1 multi_agent/research_phase1r \
  tests/test_research_phase1.py tests/test_research_launcher.py tests/test_research_phase1r.py
.venv/bin/python -W error -m compileall -q multi_agent/research_phase1r
bash -n scripts/research_phase1/phase1r.sh
git diff --check
```

After both benchmarks, root `analyze` also writes concurrency_comparison.json
and concurrency_comparison_summary.json. They check target/workload matching
and retain validity, censoring, cleanup and memory beside throughput.
