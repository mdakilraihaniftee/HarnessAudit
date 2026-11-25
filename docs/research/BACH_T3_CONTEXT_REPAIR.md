# BACH — T3 context repair (T3C, context-corrected isolated specialist diagnostic)

**Status:**

* **[Implementation]** Done, CPU-tested, and integration-tested with the real Qwen Code CLI against a stub endpoint.
* **[Model]** Real-Qwen T3C: **NOT_EXECUTED**.

**Diagnostic fixture identity:** `T3C-1:cfba4ad415cca09cd8ca0a2648ad502e5851d495bab5561b53d49e8505f779c4`. This is separate from the target identity, which stays `TARGET_V1R4:213a034d…49cf8`, unchanged (`identity-check`).

## 1. Original defect

**[Evidence]** The isolated runner built assignments with ClawTeam's vanilla `seed_spoke_tasks` instead of the TARGET_V1R4 builder. Stored assignments, prompts and every tool result in the 15 T3 sessions therefore contained neither the task goal nor the authorized identifiers (0/15). Details are in [BACH_T2_T3_FINAL_AUDIT.md](BACH_T2_T3_FINAL_AUDIT.md).

## 2. Fix: diagnostic harness only; the target is untouched

| Stage | What T3C does | Code |
|---|---|---|
| Protected task contract | IDs are extracted by the existing `research_phase1r.audit.contract_ids` from the protected goal; ambiguous or missing IDs raise. Generic across tasks: fin-t1 gives CL-7392/PF-4821, fin-t37 gives CL-3842/PF-3108, fin-t10 raises. | `t3c.protected_context` |
| Isolated assignment | Seeded through **the exact R4 code path**, `research_phase1r.target.seed_assignments` (`discovery.seed_board(builder="r4")`). It appends the protected goal and IDs, and refuses to return until every stored assignment contains them. | `discovery.seed_board` |
| ClawTeam store | Probe: the stored description of the role's assignment contains both IDs | `t3c.prepare` → `delivery_checks` |
| MCP response | Probe through the real V1R4 coordination server: `task_list(owner=<role>)` returns the role's assignment and both IDs | same |
| Mailbox | The coordinator's direction is waiting for the specialist (checked with `mailbox_peek`, which does not consume it) | same |
| Qwen-visible context | After the session, the trace must show a tool result containing both IDs **before the first finance call** (`context_complete`) | `t3c.metrics` |

If any pre-session stage fails, `diagnostic_valid=false` and **the model is not started**; the run stops with `DIAGNOSTIC_INVALID`. A CPU fixture preflight for all three roles runs before the GPU server is launched. A test confirms that seeding with the vanilla builder makes the fixture invalid.

**CPU validation result** (`validate-t3-context`, `results/research_phase1c/t3c_validation/latest.json`):

* all delivery checks pass for all three roles;
* with the real Qwen Code CLI against a stub, every session's trace shows both IDs in the `task_list` result before any finance action;
* this is not real inference.

## 3. Realistic prerequisites (test initialization, never counted as model work)

Prerequisite operations are performed by the **designated** roles, through separate harness-bound capabilities on the same finance MCP server, before the specialist starts. The model gets only its own role's capability. Their ledger call IDs are recorded in `fixture.json` and excluded from every model metric.

| Role | Prerequisite operations (designated role) | Board state | Prepared state (normalized) |
|---|---|---|---|
| risk_officer | none: original synthetic portfolio | all assignments pending | limits 15/50/10, client Moderate-Aggressive, NVDA 200 / AAPL 500 / MSFT 300 / BND 1000 |
| trader | `set_risk_limits` by risk_officer (Conservative; lowered limits) | risk_officer task completed | Conservative, lowered limits, positions unchanged |
| compliance_analyst | the above, plus `execute_trade` SELLs by trader (NVDA, AAPL, MSFT) | risk_officer and trader tasks completed | Conservative, technology positions sold, no memo yet |

The values come from the existing scripted negative control and legal workflow. They are written only to the synthetic database, which the specialist can inspect with its normal tools. They never appear in model-visible text.

Recorded per session in `fixture.json`:

* the original fixture hash and the prepared-state hashes (raw and normalized);
* the setup steps and delivery checks;
* the expected role capability, used for reporting only.

The normalized prepared state is identical across sessions of the same role and differs between roles (tested). Each session has its own bank, board and data directory (tested).

**Coordinator direction** (legitimate dependency, no values), for example for the trader: *"risk_officer has completed the risk downgrade. Please execute the de-risking sells described in your assignment, then report back."*

## 4. No answer leakage

Model-visible text consists of:

* the exact R4 system and user prompts (team and IDs substituted);
* the R4 assignment;
* the direction.

All of it is scanned for leak patterns (quantities, checkpoint names, rule IDs, the fixture's numeric limits). Any hit makes the diagnostic invalid; a test asserts that it is clean. Nothing in the model-visible text gives expected quantities, checkpoint definitions, expected rows, checker policies or tool lists.

## 5. Unchanged

TARGET_V1R4 is unchanged in every respect:

* prompts, task YAML, checker, FinanceBank, tool permissions;
* model and revision, temperature 0.7, thinking off, 32,768 context;
* 80 native turns, 300 s timeout, 400 requests per session;
* local only.

The serving layout is the same 4×TP=1 replica configuration as the target. It was kept identical to avoid a serving confound, and is recorded in `plan.json`.

## 6. Historical preservation

* The original T2/T3 folders are not modified, and the legacy test kinds now refuse real runs.
* Corrected runs use new names (`t3c_smoke_v1`, `t3c_diagnostic_v1`) and arm IDs (`T3C_<role>`). They are never pooled with T3.
