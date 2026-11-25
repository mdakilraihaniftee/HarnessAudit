# BACH — coordination repair decision and implementation (P2, P4)

## Decision: no target repair. TARGET_V1R4 remains the current target.

Decision hierarchy (§9):

| Level | Applies? | Basis |
|---|---|---|
| A. Verified implementation/schema defect | **No** | Live filter matrix 13/13 equals ground truth on both servers. The schema publishes optional filters, enum values, unfiltered usage and empty-result semantics. Qwen Code forwards them unchanged. Priority `medium` is the documented default. |
| B. Correct interface, incorrect model queries | **Yes** | 483/487 coordinator queries filtered on `priority="high"`, 0 were unfiltered, and the existing overview tool was never used. A controlled read-only capability test (T2) is implemented and ready for manual launch. |
| C. Model-capability limitation | **Likely, not yet isolated** | The same behaviour in 15/15 trials, at temperature 0.7, despite explicit instructions. T2 separates it from the R4 schema side-effect (H3) and from urgency wording (H4). |

Stop conditions reached:

* **§25.1:** the interface is correct, but Qwen repeatedly misuses it.
* **§25.8:** the known failure, "the coordinator cannot discover assignments", persists after the justified, controlled R4 repair.

Target modification has therefore stopped pending PI review. **No new target identity was frozen.**

## Legitimate-workflow defects checked (P4)

| Candidate defect | Checked | Result |
|---|---|---|
| Dropped assignment content | The assignment text in the task file equals what the model received in its `task_list` result (45/45 contain the ids) | none |
| Incorrectly registered tools | 26 coordination tools, identical names on both servers; finance tools as registered | none |
| Malformed task-board state | The live board equals the task files; specialists claimed tasks 45/45 | none |
| Lost mailbox messages | 56/56 sends delivered (`is_error=false`); late delivery explained by timing | none |
| Trusted sender/receiver metadata | Acting role comes from the harness-bound capability; ledger and trace agree | none |
| MCP transport errors | 0 transport errors; 64 native tool errors are argument errors (team name, enums) or domain rejections | none |
| Task lifecycle defects | `pending → in_progress → completed` works (scripted workflow) | none |
| Premature session shutdown | 5 coordinator sessions hit the unchanged 80-turn limit; 1 hit the 32,768-token context limit (B1p-3). Both are limits, not defects. | none |
| Tool-schema mismatch | none (see A) | none |

Nothing met the §11 bar (root cause, original behaviour, code change, expected corrected behaviour, unit and integration tests, identity impact). So no target code was changed.

## What was implemented (research tooling only, not part of any target)

All new code lives in `multi_agent/research_phase1c/`. That directory is outside every source set hashed into TARGET_V1R…V1R4, so `identity-check` still reports the frozen V1R4 identity.

| Component | File | Purpose | Tested |
|---|---|---|---|
| Independent raw-trace parser and R4 audit | `common.py`, `audit.py` | P0 reconciliation; P1/P3/§14 analyses | CPU |
| Observation-only coordination labels | `audit.coordination_labels` | `FILTERED_EMPTY`, `REPEATED_QUERY`, `INVALID_ARGUMENT_LOOP`, `NO_PROGRESS`, `PRODUCTIVE_POLLING`, `SESSION_TURN_EXHAUSTED`, distinct board states, progress events | CPU (8 tests) |
| Live task-board filter matrix | `discovery.py` | §8.2 on both servers vs ground truth | CPU, scripted integration |
| Fresh controls and scripted legal workflow | `controls_run.py` | §10.4, §15 | scripted integration |
| Isolated coordinator (T2) and specialist (T3) runners | `isolated.py` | manual real-Qwen diagnostics | CPU stub with the real Qwen Code CLI; **not live-tested** |
| G1–G8 evaluator | `gates.py` | §18, thresholds unchanged | CPU |
| Bundle and stdlib validator | `bundle.py`, `scripts/research_phase1/validate_phase1_completion_bundle.py` | §23 | CPU |
| Launcher | `scripts/research_phase1/phase1_completion.sh` | §20 | CPU (launcher tests) |

The labels are observations only. Nothing rewrites arguments, injects messages or terminates agents; a test asserts that the audit code issues no tool calls.

A defect found and fixed in the new tooling itself: concurrent board seeding raced on the process-wide `CLAWTEAM_DATA_DIR`. It is now serialized and regression-tested.

## Options that require PI approval (prepared, not implemented or activated)

1. **Filtered-empty result hint (new model-visible affordance).**
   * *Change:* when a filtered `task_list` returns no tasks on a non-empty board, the V1R4 contract server would add a text block such as *"No task matched status=…, priority=…; the team board has N tasks. Call task_list with only team_name to list them."* It would expose only a count the coordinator can already obtain.
   * *Status:* not implemented. It changes tool results, would need a new identity (TARGET_V1R5) and is not supported until T2 shows the schema/wording arms do not explain the behaviour.
2. **Stop publishing the `priority` enum only.** Only if T2 shows that the published enum *causes* the `high` filter (H3). It would re-hide accepted values, partly reverting the R4 repair, so it is a PI trade-off, not a defect fix.
3. **Team name without the model slug** (H7: 38+ `Team not found` errors from dropping "8B"). This is an orchestration change and needs a new identity. Its value is unproven, because every such error was followed by a corrected call.
4. **Separately versioned stronger local model for the coordinator, or for all roles** (Level 3, §19). Same task, tools, prompts, policies and checks. Requires an explicit cached-weights step and PI approval; see the decision document.

Explicitly **not** done:

* seeding tasks as high priority;
* removing or ignoring model-specified filters;
* rewriting tool calls;
* injecting board contents;
* answer hints;
* mechanical role enforcement;
* more prompt text.
