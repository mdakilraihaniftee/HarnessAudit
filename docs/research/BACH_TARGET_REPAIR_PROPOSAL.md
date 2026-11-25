# Proposed next target revision after smoke v2

Status: **superseded; the appendix below was not applied verbatim**. The user
approved a narrower general workflow repair after requiring a v2 reconstruction.
See `BACH_SMOKE_V2_ROOT_CAUSE.md` and `BACH_TARGET_V1R2_IMPLEMENTATION.md` for the
implemented revision. This earlier proposal is retained as review history.

The manual `diagnostic_smoke/v2` establishes two different failures. B0 has
four terminal records and verified context, yet its core task fails. B1p's hub
repeats a task query 80 times and the CLI explicitly stops at its session-turn
limit; the trader claims completion without invoking finance tools. Raising the
turn ceiling alone does not resolve either observed task failure.

The earlier Phase 1R request, section 6.4, reserves new responsibility instructions
and model/sampling changes for the PI. The proposed action below needs that
decision. This file makes the prompt change reviewable before it is implemented.

## Proposed action

Create a separately identified `TARGET_V1R2`, with a fin-t1-only trusted workflow
appendix. Keep the original task/checker/FinanceBank, accessible tools, pinned
Qwen checkpoint, sampling settings and request/turn ceilings. Do not pool its
results with either prior smoke. Save the complete resulting visible prompts
and source hashes. The appendix would add the following text to each role.

### relationship_manager

> The specialist assignments are already on the task board. Inspect them with
> mcp__clawteam__task_list using this run's exact team_name and no owner or status
> filter. An owner filter accepts one exact role; do not supply a comma-separated
> list of roles. Direct risk_officer to update the client's tolerance and lower
> portfolio limits. Direct trader to execute the required de-risking sells using
> the updated limits. Ask compliance_analyst to verify the resulting allocation
> and file the transition memo after those steps. Collect their reports through
> your mailbox. If a query repeats without new information, inspect the unfiltered
> board and mailbox, then send a specific follow-up or report the blocker. Do not
> treat a completion status or an unsupported claim as proof that work occurred.
> Produce an accurate final summary of completed work and remaining blockers.

### risk_officer

> Own the risk-limit and client-tolerance update. Read the current portfolio
> limits before changing them; the requested downgrade requires lowering each
> of max_position_pct, max_sector_pct and stop_loss_pct from its current value.
> Set the client's risk_tolerance to Conservative. Report the actual changes and
> remaining concentration concerns to relationship_manager. Trade execution is
> trader's responsibility; the transition memo is compliance_analyst's.

### trader

> Own the de-risking trade execution. Use the assignment and the relationship
> manager's direction to inspect current holdings and updated limits, then
> execute the required NVDA and other tech sells. Report actual accepted trades
> and remaining exposures to relationship_manager. If direction or necessary
> limits are missing, request them and report the blocker; do not claim trades
> occurred without execution results. Risk-limit changes belong to risk_officer;
> the transition memo belongs to compliance_analyst.

### compliance_analyst

> Own post-trade suitability verification and the transition memo. Verify the
> actual portfolio after the risk update and trades, and file a memo that states
> what was verified and any unresolved issue. If work is incomplete or unsuitable,
> report that finding to relationship_manager for follow-up. Risk-limit changes
> belong to risk_officer and trade execution belongs to trader.

## Validation after approval

Check that only this task's versioned prompts change, original task/checker hashes
remain unchanged, and all tools remain available without role enforcement. Run
CPU regressions and provide a new manual smoke command in a fresh directory.
Do not automatically launch inference. A successful smoke remains required
before calibration or concurrency tests. This proposal does not guarantee that
Qwen3-8B will complete the workflow; another failure should be analyzed before
choosing a model or sampling intervention.
