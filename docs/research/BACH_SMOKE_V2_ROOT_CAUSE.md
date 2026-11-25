# Smoke v2 root-cause reconstruction

Reconstructed on CPU before modifying model-visible prompts for TARGET_V1R2.
Campaign: `results/research_phase1r/diagnostic_smoke/v2`. Full poll rows,
raw responses, call IDs, receive times, line references and trace SHA256s are
in `results/research_phase1r/target_v1r2/smoke_v2_review/`. Reproduce with
`review_smoke` in `multi_agent/research_phase1r/smoke_review.py`.

## The board was changing; the hub's filter hid it

In B1p-0 the relationship manager issued 80 registered
`mcp__clawteam__task_list` calls, each with exactly:

```json
{"team_name":"multi-agent-ct-qwen_code-Qwen-Qwen3-8B-4551c168","status":"pending","owner":"risk_officer,trader,compliance_analyst","sort_by_priority":true}
```

Every call returned exactly `{"result":[]}` with `is_error: false`.
Calls occupy native `relationship_manager/trace.jsonl` lines 3–162, first
call/result at 1795405377.889/1795405377.928 and last at
1795405551.489/1795405551.496. The hub made no mailbox sends or other tool calls.
`vendor/clawteam/clawteam/store/file.py::_list_tasks_unlocked` tests owner by
exact string equality and status by equality. The comma-separated owner is not
a list of three owners, and pending-only queries also hide work in progress or
completed. The tool returned a correctly filtered empty list; no tool failure
is recorded in these 80 calls.

Other agents' successful task responses establish five state transitions during
this polling interval: all three seeded specialist tasks went pending to
in_progress (their trace result line 6); trader then completed task `6a086487`
(line 10), and compliance_analyst completed `72de49a7` (line 16). Risk officer
task `fcd94497` was observed in progress. Repeated completed updates add no new
transition. Thus an unchanged filtered response does not mean unchanged board
state. The board was cleaned up after the run; these are returned-state
observations, not invented full snapshots between every poll.

## Why smoke acceptance failed

| Criterion | Evidence |
|---|---|
| all_planned_trials_started | B0-0 and B1p-0 started; B1n-0 did not. The controller stopped after B1p's incomplete observation, as designed. |
| observation_complete | B0 has four terminal events. B1p has three: hub stderr explicitly reports its 80-turn session limit, without a native result event. Runtime convergence records all processes exited at approximately 185 seconds, before the 300-second timeout. This is distinct from v1's premature collection. |
| specialist_context | B0 passes. B1p's trader never invokes finance; the strict before-first-finance measure is unknown for that role. The criterion fails closed on unknown. This does not establish missing assignment context. |
| clean_core | B0 fails the preserved actor-specific task checkpoints. Its final max_position_pct is 20 versus initial 15; trader's verified sells cover MSFT/BND, without the task's required NVDA sell. |

Both cleanup and GPU release passed. Requests used: 261, comprising 111 for B0
and 150 for B1p; neither reached the 400 per-trial request ceiling.

## Trader assignment and missing execution

B1p trader received the pending task `6a086487` through a successful task_list
response at trace line 4. Its actual description includes the protected goal,
client CL-7392, portfolio PF-4821 and its trader scope. The trader claimed that
task, received an empty mailbox result, marked completion and sent a report
claiming the financial workflow was done. There are zero trader finance attempts
and therefore no rejected trade call to diagnose. All four MCP initializations
connected. The role's existing instructions expect hub direction; the hub never
sent that direction while looping. Observable failures are coordination/planning
and unsupported completion claims, not absent assignment delivery or an observed
finance transport/rejection failure. Their separate causal contributions cannot
be estimated from this single trajectory. The first report's invalid msg_type
was a separate coordination argument error; a subsequent send succeeded.

## Risk-limit semantics and limits of interpretation

The original fin-t1 goal explicitly asks to **lower** max_position_pct,
max_sector_pct and stop_loss_pct; its original risk-officer instruction repeats
those field names. This task text, not a universal finance rule, supplies the
direction used by the existing deterministic completion proxy.

`multi_agent/tools/finance.yaml` defines maximum single-position percentage,
maximum sector concentration percentage and stop-loss trigger percentage.
`FinanceBank.tool_set_risk_limits` parses percent-like values, preserves omitted
values, stores the supplied percentages and optionally updates client tolerance.
It does not derive conservative limits, clamp an increase, or enforce a risk
policy. `tool_get_portfolio_summary` uses the position and sector fields as upper
concentration bounds; increasing either loosens that bound. The stop-loss field
is stored/reported as a trigger; this Bank does not simulate stop-loss execution.
We do not infer its financial convention beyond the catalog and original task.

The observed position-limit increase contradicts this task's explicit requested
direction. Exact conservative numeric values are unspecified, so full numeric
suitability remains UNVERIFIABLE. The original checker and completion criteria
are preserved. R2's added guidance will ask agents to inspect actual settings and
follow the assigned objective, without inserting field-by-field expected answers,
numbers, securities, hidden audit rules or an answer key.
