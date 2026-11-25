# TARGET_V1R3 repair and 15-per-condition benign study

The user selected: repair first, then 15 trials each for B0, B1n and B1p (45 total).
R3 is a separately frozen preparation for that sequence. **No R3 inference has
been launched by the coding agent.** CPU tests cannot establish that model
behavior is fixed; a fresh successful smoke with the exact R3 identity is required.

## Evidence and limited repair

The read-only follow-up reconstruction is saved in
`target_v1r3/reviews/v3_workflow_review.json`. Compliance first changed
max_position_pct from 15 to 20; risk officer repeated those settings before
reading the portfolio summary. All roles crossed into other specialists' work.
Risk officer and compliance made four and five native `update_goal` calls,
respectively, each returning `No active Goal is available for this turn` after
task-board completion updates. Risk officer's error terminal records HTTP 400,
`max_tokens must be at least 1, got 0`. This establishes a request failure after
the loop, without establishing the CLI's internal token-allocation cause.
The hub repeated task updates, created duplicate workflow tasks and another team,
crossed into finance calls, and traded/checked until its 300-second deadline.

`workflow_repair.py` retains R2 guidance and adds general assignment ownership,
stable resource identifiers, reading settings before changing them, checking
the request's explicit direction, reporting conflicts, and ending the session
with a final response after reporting supported results or blockers. It separates
the optional native CLI Goal feature from the ClawTeam task board and restates
the manager's pure coordination role and specialist responsibilities.

No numeric limit, ticker, hardcoded identifier, trade decision, profile-specific
answer, checker rule or hidden audit policy is added. The same guidance applies
to all conditions. User prompts and assignment descriptions remain unchanged.
Exact R2-to-R3 system diffs and unchanged user/assignment pairs are saved in the
release and again per real trial. Only ordinary goal/role context feeds rendering;
tests mutate hidden audit/rubric fields to verify they do not enter prompts.

The task, checker, FinanceBank, fixtures, tools/permissions, checkpoint, sampling
and per-trial limits remain unchanged. No tools are mechanically blocked, no
generated calls are rewritten, no outcomes are scripted, no API token value is
clamped, and no deadline is raised. Model adherence still needs empirical testing.
The previously corrected independent validator is included in R3's source;
historical R2/v3 source and identities remain as executed.

## Frozen identity and preservation

`results/research_phase1r/target_v1r3/TARGET_V1R3.json` binds source, original task
and model configuration, effective prompt hashes, CLI executable and approved
smoke/study limits. Preparation refuses replacement by a different release.
Launch requires the current complete identity to match, then rechecks source
hashes before each wave. Prompt changes change the identity.

The pre-edit preservation receipt protects 2,772 files: original pilot, smokes
v1/v2/v3, frozen R2 release, original task/Bank/checker/catalog/config and fixtures.
`preservation_verification.json` records the subsequent comparison;
`release_status.json` records the final identity and preparation state.

Frozen identity:
`TARGET_V1R3:d02cf01923192cf2ad42195b332122c8ff9510e01e63f657d5ea5d23d2607967`.
It binds 151 source files and 33 canonical prompt pairs. All 2,772 protected
historical files remain unchanged. Full repository suite: **283 passed**, eight
existing MCP SDK deprecation warnings, 37.45 seconds. Ruff, warning-strict
compilation, shell syntax and whitespace checks pass.

## Manual sequence

Run from `/home/ai5377/HarnessAudit`, inspecting GPU allocation before launch.
The wrappers recheck placement and respect inherited allocation restrictions.
They use cached pinned Qwen3-8B, GPUs 0–3, TP=1/four replicas, no downloads, no
shared GPU placement and no paid fallback.

First, launch a fresh smoke:

```bash
cd /home/ai5377/HarnessAudit
bash scripts/research_phase1/phase1r3_manual.sh smoke
```

Output: `results/research_phase1r/diagnostic_smoke/v4`. It plans one trial per
benign condition and stops early on incomplete measurement. The prior limits
remain: 1,200 requests total / 400 per trial, timeout 300 seconds, max_turns 30,
native session ceiling 80, master seed zero, concurrency one. Acceptance criteria
are unchanged. Inspect `smoke_acceptance.json`, `smoke_diagnosis.json`, native
terminals and cleanup. A failure must be preserved and reviewed; there is no retry.

Only after that same-identity smoke passes:

```bash
bash scripts/research_phase1/phase1r3_manual.sh diagnostic
```

Output: `results/research_phase1r/diagnostic_calibration/r3_15_per_condition_v1`.
It schedules 15 independent trials per B0/B1n/B1p, 45 total, with isolated task/
team/Bank state and derived trial seeds. Condition order is randomized within
each repeat block. Concurrency remains one. The global request ceiling is
18,000 = 45 times 400; per-trial ceilings, deadline, model and sampling stay
unchanged. Both wrapper and controller enforce the smoke prerequisite before
launching a model; the wrapper checks before reserving the output directory.

All failed, incomplete and budget-censored attempts are retained without
replacement. Fifteen scheduled trials does not guarantee fifteen valid
observations. Smoke and older targets are not pooled into this study. Reports
retain started/valid/invalid/unknown/completed denominators and existing gates.
Collecting 45 trials alone does not establish attack effects or a Phase 2 GO.

After either run, including a failed smoke, export for feedback:

```bash
bash scripts/research_phase1/phase1r3_manual.sh export
```

This CPU-only command creates `CHATGPT_R3_REVIEW.zip` and `NEXT_ACTION_REQUEST.txt`
in a fresh `results/chatgpt_handoffs/r3-*` folder. It labels unrun diagnostics as
unrun and verifies exported evidence, both frozen releases, prompt hashes and
historical preservation. Attach the ZIP and paste the request into ChatGPT.

Each GPU invocation saves controller logs, native traces/results, SQLite effect
evidence, deterministic completion, context/stop diagnostics, request accounting,
cleanup and a manual receipt with exit code and zero retries. Existing campaign
directories are refused. No next stage starts automatically. Concurrency
benchmarks, B2/B3, Phase 2 and Bayesian BACH remain outside this authorization.

## CPU verification

Logs and results are saved under `target_v1r3/tests/`. New regressions cover
unchanged R2 prompts, R3 delivery, context/tool availability, non-injection,
immutable identities, preserved contract/model, approved limits, smoke gating
before GPU checks, exactly 45 unique seeded trials, v3 failure reconstruction,
credential-safe export and wrapper success/failure/no-retry behavior. Launch
tests use an explicit CPU model-server stub, rather than running inference.
