# BACH — final manual run guide (pre-Phase-2)

All commands run from `/home/ai5377/HarnessAudit` through `scripts/research_phase1/phase1_completion.sh`.

**Run rules:**

* **CPU commands** never start a model.
* **Real commands** need `RUN_REAL=YES` and a fresh folder (`OUT_TAG=v2`, `v3`, … for repeats). They never retry or replace a session, and they rebuild the reviewer bundle afterwards.
* **Unchanged:** Qwen3-8B at the pinned revision, temperature 0.7 (server-enforced), thinking off, 32,768 context, 80 native turns, 300 s agent timeout, 400 requests per session, local only, no downloads, no paid fallback.
* **Do not** commit or edit tracked files before a run.

## 1. CPU-only (one command)

```bash
cd /home/ai5377/HarnessAudit && bash scripts/research_phase1/phase1_completion.sh cpu-all
```

This runs:

* `audit-t2-t3`: reconstructs T2/T3 from raw evidence;
* `validate-t3-context`: fixture preflight for all roles, authorized IDs, MCP payloads, prerequisite states, and the real Qwen Code CLI against a stub;
* the Phase 1 completion tests (93);
* `identity-check`: TARGET_V1R4 fingerprint and preservation.

Individual commands: `audit-t2-t3`, `validate-t3-context`, `test-t3-fixtures`, `analyze-t2`, `analyze-t3`, `test-cpu` (full regression, ruff, compileall, `bash -n`, `git diff --check`).

## 2. GPU preflight (read-only)

```bash
cd /home/ai5377/HarnessAudit && nvidia-smi && bash scripts/research_phase1/phase1_completion.sh gpu-preflight
```

Checks GPUs 0–3: UUID mapping, free memory, running processes, the pinned local checkpoint, and that downloads and paid fallback are disabled. The run re-checks before starting its own server and refuses occupied GPUs. Server ownership and cleanup are recorded.

## 3. Corrected T3 smoke (1 session per role, 3 in total)

```bash
cd /home/ai5377/HarnessAudit && RUN_REAL=YES bash scripts/research_phase1/phase1_completion.sh t3-smoke
```

* **Output:** `results/research_phase1c/isolated/t3c_smoke_v1/`.
* **Before the GPU:** runs a CPU fixture preflight first and stops before any model call if a fixture is invalid.
* **During:** stops further sessions if any session's fixture is invalid.
* **Check afterwards:** `isolated_summary.json → arms.T3C_<role>.t3c`. `context_complete` must be 1 per role, and no session may be `NO_OPPORTUNITY` or `MISSING_EVIDENCE`. If any is, stop and report (Case D).

## 4. Corrected T3 diagnostic (5 per role, 15 in total)

```bash
cd /home/ai5377/HarnessAudit && RUN_REAL=YES bash scripts/research_phase1/phase1_completion.sh t3-diagnostic
```

It refuses unless `t3c_smoke_v1` (or `T3C_SMOKE_DIR`) is a structurally valid real smoke. Output goes to `results/research_phase1c/isolated/t3c_diagnostic_v1/`. All sessions are kept, including failures. It uses at most 6,000 requests and takes roughly 10–20 minutes plus server start.

## 5. Optional T2 follow-up (urgency only; not needed for the next decision)

```bash
cd /home/ai5377/HarnessAudit && RUN_REAL=YES bash scripts/research_phase1/phase1_completion.sh t2-followup
```

20 sessions (R4 schema, corrected builder; original vs urgency-only wording, 10 each). Output: `results/research_phase1c/isolated/t2u_urgency_v1/`.

## 6–7. Analysis and readiness (CPU)

```bash
cd /home/ai5377/HarnessAudit && bash scripts/research_phase1/phase1_completion.sh analyze-t3 && bash scripts/research_phase1/phase1_completion.sh analyze && bash scripts/research_phase1/phase1_completion.sh evaluate-gates
```

## 8. Reviewer bundle

```bash
cd /home/ai5377/HarnessAudit && bash scripts/research_phase1/phase1_completion.sh package
```

This writes `results/chatgpt_handoffs/pre_phase2_final_<timestamp>-<id>/CHATGPT_BACH_PRE_PHASE2_FINAL.zip` and `validation_receipt.json`. Every real isolated run is collected automatically, and T3C is marked `NOT_EXECUTED` until it exists. Re-validate with `python3 validate_bundle.py <zip>`.

## Blocked on purpose

| Command | Why it is blocked |
|---|---|
| `test-coordination`, `test-specialists` | Superseded: they used the defective builder |
| `smoke-b0` | Needs a new target mode |
| `calibration`, `concurrency` | Need a passing smoke plus PI approval |
| `smoke-benign` | Runs the unchanged R4 three-arm smoke; not recommended: no discriminating hypothesis |
