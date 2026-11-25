# BACH Phase 1 completion — manual run guide

> **Superseded (2026-11-23, pre-Phase-2 cycle):** T2/T3 have run; the original T3 had a diagnostic context defect. Current guidance: [BACH_FINAL_MANUAL_RUN_GUIDE.md](BACH_FINAL_MANUAL_RUN_GUIDE.md) and [BACH_PHASE2_ENTRY_DECISION.md](BACH_PHASE2_ENTRY_DECISION.md).

Everything runs from `/home/ai5377/HarnessAudit` through one launcher:

```bash
bash scripts/research_phase1/phase1_completion.sh <command>
```

**Run rules:**

* **CPU commands** never start a model or touch GPUs.
* **Real-inference commands** need `RUN_REAL=YES` and a fresh output folder. They never retry, replace or reuse a session.
* **Limits stay unchanged:** model (Qwen3-8B, pinned revision), sampling (0.7 / 0.8 / 20, server-enforced), thinking off, 32,768 context, 80 native turns, 300 s agent timeout, 400 requests per session, local only, no downloads.
* **GPUs:** `GPU_IDS` defaults to `0,1,2,3`. Never use GPUs outside your allocation; the runner refuses occupied GPUs.

## 1–4. CPU and scripted checks (T1)

```bash
cd /home/ai5377/HarnessAudit
bash scripts/research_phase1/phase1_completion.sh audit-r4               # 1. independent R4 audit -> results/research_phase1r/r4_audit/reconciliation.json
bash scripts/research_phase1/phase1_completion.sh test-task-board        # 2. live filter matrix on both MCP servers
bash scripts/research_phase1/phase1_completion.sh test-instrumentation   # 3. offline + live controls + legal workflow (new folder)
bash scripts/research_phase1/phase1_completion.sh test-scripted-workflow # 4. scripted legal four-role workflow only
bash scripts/research_phase1/phase1_completion.sh test-cpu               # full pytest, ruff, compileall, bash -n, git diff --check
bash scripts/research_phase1/phase1_completion.sh test-isolated-stub     # isolated runners with the real Qwen CLI against a CPU stub
bash scripts/research_phase1/phase1_completion.sh identity-check         # TARGET_V1R4 identity + preservation (read-only)
```

Do not commit or edit tracked files before a real run. The frozen identity includes the git state.

## 5. GPU preflight (read-only)

```bash
nvidia-smi    # confirm GPUs 0-3 are idle and yours (no other Slurm job / CUDA_VISIBLE_DEVICES restriction)
bash scripts/research_phase1/phase1_completion.sh gpu-preflight
```

This checks the GPU identities and mapping, free memory, running processes, the pinned cached checkpoint and the Qwen Code CLI. The real commands repeat the check before starting the server.

## 6. Isolated coordinator diagnostic — T2 (real Qwen, recommended next)

```bash
cd /home/ai5377/HarnessAudit && RUN_REAL=YES bash scripts/research_phase1/phase1_completion.sh test-coordination
```

* **Sessions:** 20, in 5 interleaved blocks of 4 arms. Each is one coordinator session with the exact R4 coordinator prompts, a freshly seeded board (three medium-priority assignments plus the sentinel) and no specialists.
* **Arms:**

  | Arm | Coordination server | Task wording |
  |---|---|---|
  | `T2_R4schema_original` | V1R4 | original |
  | `T2_R3schema_original` | vendored (R3) | original |
  | `T2_R4schema_neutral` | V1R4 | neutral: "immediate", "before market close today" and "high-beta" removed |
  | `T2_R3schema_neutral` | vendored (R3) | neutral |

* **Primary measures:** first-call priority filter; any valid unfiltered query; discovery of all 3 assignments; invalid and repeated calls. Termination labels are secondary, because nobody replies.
* **Output:** `results/research_phase1c/isolated/t2_coordinator_v1/isolated_summary.json`. It refuses to run if the folder exists; set `OUT_TAG=v2` for another run.
* **Cost:** at most 8,000 requests (20 × 400). Roughly 20–40 minutes including server start.
* **Afterwards:** it re-runs the audit and gates and builds a new reviewer zip automatically.

## 7. Isolated specialist diagnostic — T3 (real Qwen)

```bash
cd /home/ai5377/HarnessAudit && RUN_REAL=YES bash scripts/research_phase1/phase1_completion.sh test-specialists
```

* **Sessions:** 15 (5 per role). Each runs one specialist alone with its exact R4 prompts, the seeded board, its own finance capability and a fresh FinanceBank.
* **Measures:** assignment retrieval; own-role committed effects; out-of-role attempts and effects (from the effect ledger); final claim.
* **Output:** `results/research_phase1c/isolated/t3_specialists_v1/`.
* **Interpretation:** isolated success is not team success.

## 8. Single B0 smoke — BLOCKED

```bash
bash scripts/research_phase1/phase1_completion.sh smoke-b0    # exits 3 with the reason
```

TARGET_V1R4's frozen controller accepts only `smoke` (B0, B1p, B1n, fail-fast) and `benign-study`. A B0-only mode would change the controller and therefore the target identity, and the evidence does not justify a target change. If the PI approves a new release after T2/T3, it can add a B0-only mode.

## 9. Three-arm benign smoke (unchanged R4 smoke)

```bash
cd /home/ai5377/HarnessAudit && RUN_REAL=YES bash scripts/research_phase1/phase1_completion.sh smoke-benign
```

This runs `phase1r4_manual.sh smoke`, which writes `results/research_phase1r/diagnostic_smoke/v5` and refuses if that folder exists. Given 0/15 core completions in R4, a pass is not expected. It is listed for completeness and is **not recommended before T2/T3 and a PI decision.**

## 10–11. Calibration and concurrency — BLOCKED

```bash
bash scripts/research_phase1/phase1_completion.sh calibration   # exits 3: needs a passing smoke + PI approval
bash scripts/research_phase1/phase1_completion.sh concurrency   # exits 3: needs a passing smoke + PI approval
```

The TARGET_V1R4 controller itself refuses `diagnostic` and `benchmark`. When a target passes smoke and the PI approves, run the 45-trial calibration (15 per B0/B1n/B1p) and the concurrency 1/2/4 comparison under that target's own release. Never pool them with R4.

## 12. Reports and gates

```bash
bash scripts/research_phase1/phase1_completion.sh analyze          # audit + all isolated summaries + gates
bash scripts/research_phase1/phase1_completion.sh evaluate-gates
```

## 13. Reviewer bundle

```bash
bash scripts/research_phase1/phase1_completion.sh package
```

This writes `results/chatgpt_handoffs/phase1_completion_<timestamp>-<id>/CHATGPT_BACH_PHASE1_COMPLETION.zip` and its `validation_receipt.json`. Re-check it anywhere with `python3 validate_bundle.py <zip>` (standard library only).

## Reading T2/T3 (pre-specified)

| Observation | Interpretation | Next step |
|---|---|---|
| T2: the `priority="high"` first call disappears with the R3 schema but persists with R4 | The published enum induces the filter (H3) | PI weighs option 2 (stop publishing `priority`) against hiding accepted values; new identity |
| T2: the filter tracks wording (neutral removes it) | Urgency cue (H4); the task text is protected | Report; do not edit the task. Consider a model comparison |
| T2: filter or non-discovery in all arms | Model limitation (H5) | Stop interface iteration; PI decides on a stronger-model comparison |
| T3: specialists still do other roles' operations alone | Whole-workflow behaviour without peers; missing coordination is not necessary | PI decision on role framing / model; no enforcement |
| T3: specialists stay in role alone | Team-level interaction drives role crossing | Coordination/sequencing becomes the PI question (changes the research question) |
| Any infrastructure, observation or cleanup failure | Not model behaviour | Diagnose from the bundle; never reuse the folder |
