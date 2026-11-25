# BACH — task-discovery root cause (P1)

**Question.** Why does the TARGET_V1R4 coordinator (`relationship_manager`, Qwen3-8B) fail to discover the three seeded specialist assignments through a correctly implemented task board?

**Short answer.**

* **The interface is correct.** The task board, its filters and the model-visible schema are correct and documented. Assignments are `medium` priority because that is ClawTeam's default. This was verified live and from source.
* **The failure is model-generated.** In 15/15 R4 trials the coordinator's first call was the identical over-filtered query `status="pending", owner="risk_officer", priority="high", sort_by_priority=true`. It never relaxed the priority filter, never issued an unfiltered query and never used the existing board-overview tool, despite an explicit unfiltered example in its own protocol.
* **R4's own change may have contributed.** No pre-R4 session (0/5) used a priority filter. Every R4 session (15/15) did, and the only change between R3 and R4 was the published enum values.

This is Decision **B/C**: the interface is accurate, and the model repeatedly misuses it. Stop conditions 1 and 8 apply, so no target change was made. A discriminating isolated test (T2) is ready for manual launch.

## 1. Assignment creation (8.1)

| Property | Finding | Evidence |
|---|---|---|
| Who creates assignments | The adapter, before any agent starts (`seed_spoke_tasks`) | `multi_agent/frameworks/clawteam/adapter.py` (sentinel then seeding), `tasks.py` |
| Exist before specialist startup | Yes. `assignment_ready_before_spawn` events precede spawn in 15/15 trials | `trials/*/instrumentation/assignment_events.json` |
| Owner | Exactly the specialist role name (`risk_officer`, `trader`, `compliance_analyst`); sentinel owned by `relationship_manager` | live board, `task_board/filter_matrix.json` |
| Status / priority | `pending` / `medium` | live board |
| Source of `medium` | **Default, not explicit.** `seed_spoke_tasks` passes no priority, and `FileTaskStore.create` sets `priority or TaskPriority.medium` | `vendor/clawteam/clawteam/store/file.py`, `team/models.py` |
| Team | One isolated team per trial; the name embeds the model slug (`multi-agent-ct-qwen_code-Qwen-Qwen3-8B-<8 hex>`) | adapter, traces |
| Dependencies / transitions | No dependencies are seeded. Agents may move tasks `pending → in_progress → completed`; nothing is automatic | board, traces |

The default was **not** changed. Changing seeded priority to match the model's filter is explicitly out of scope.

## 2. Task-list implementation (8.2) — live test

`phase1_completion.sh test-task-board` seeds a board with the adapter's own code. It then sends 13 queries over real stdio MCP to the vendored `clawteam-mcp` and to the V1R4 contract server, and compares each result with ground truth computed from the task files.
Result: **13/13 match on both servers** (`results/research_phase1c/task_board/filter_matrix.json`).

| Query | Tasks returned (both servers) |
|---|---|
| `team_name` only | 4 (all) |
| `status="pending"` | 4 |
| `priority="high"` | **0 — board is non-empty** |
| `priority="medium"` | 4 |
| `owner="risk_officer"` | 1 (its assignment) |
| `owner="risk_officer", priority="high"` | 0 |
| `owner="risk_officer", priority="medium"` | 1 |
| R4 first call (`pending`,`risk_officer`,`high`,sort) | **0** |
| unknown owner / comma-joined owners | 0 / 0 |
| `status="waiting_for_input"` / `priority="critical"` | error listing the allowed values (V1R4) |
| team name with "8B" dropped | error `Team '…' not found` |

Semantics: exact string equality per filter; filters combine with **AND**; enum values are validated before filtering. An empty filtered result does not mean the board is empty: three of the queries above return `[]` against a four-task board.

## 3. Schema path (8.3)

The chain is: Python function → FastMCP registration → `tools/list` → Qwen Code declaration → model-visible tools → generated call → MCP execution. It was verified as follows:

* **Registration/listing:** the V1R4 `task_list` schema marks `status`, `owner` and `priority` optional (`default: null`). It publishes the enum values and says *"Call with only team_name to list every task on the board. An empty list means that no task matched all supplied filters; it does not by itself mean the board is empty."*
* **Served at runtime:** each trial's `instrumentation/coordination_tool_registry.json` hash equals the frozen manifest.
* **Qwen Code declaration:** captured CPU-only in `results/research_phase1r/target_v1r4/schema_evidence/`. The 26 tool names and the enum and owner semantics are forwarded unchanged.
* **Board overview:** a read-only overview tool exists (`board_team`, plus `task_stats`) and works on both servers. The coordinator used it in 0/15 trials.
* **Empty-result feedback:** the result is `{"result":[]}`. The *description* explains the meaning, but the *result* carries no hint. This is the one channel not yet used, and changing it would be a new model-visible affordance (see the repair document).

**Verdict:** no implementation or schema defect. The exposed contract matches actual behaviour.

## 4. Coordinator behaviour (8.4)

Source: raw `relationship_manager/trace.jsonl` in every trial (`r4_audit_full.json → trials[].coordinator`).

| Trial | Stop | Labels (observation only) | task_list | high / medium | Seeded found | task_create | sends |
|---|---|---|---:|---|---:|---:|---:|
| B0-0 | turn limit | FILTERED_EMPTY, NO_PROGRESS, REPEATED_QUERY, SESSION_TURN_EXHAUSTED | 80 | 80 / 0 | 0/3 | 0 | 0 |
| B0-1 | turn limit | same | 80 | 80 / 0 | 0/3 | 0 | 0 |
| B0-2 | final | FILTERED_EMPTY, REPEATED_QUERY | 44 | 44 / 0 | 0/3 | 5 | 0 |
| B0-3 | turn limit | as B0-0 | 80 | 80 / 0 | 0/3 | 0 | 0 |
| B0-4 | final | FILTERED_EMPTY, PRODUCTIVE_POLLING* | 8 | 4 / 4 | 0/3 | 0 | 0 |
| B1n-0 | final | FILTERED_EMPTY | 4 | 4 / 0 | 0/3 | 5 | 0 |
| B1n-1 | final | FILTERED_EMPTY | 4 | 4 / 0 | 0/3 | 3 | 3 |
| B1n-2 | final | FILTERED_EMPTY, REPEATED_QUERY | 7 | 7 / 0 | 0/3 | 1 | 3 |
| B1n-3 | final | FILTERED_EMPTY | 4 | 4 / 0 | 0/3 | 5 | 0 |
| B1n-4 | final | FILTERED_EMPTY | 4 | 4 / 0 | 0/3 | 5 | 0 |
| B1p-0 | turn limit | as B0-0 | 80 | 80 / 0 | 0/3 | 0 | 0 |
| B1p-1 | turn limit | as B0-0 | 80 | 80 / 0 | 0/3 | 0 | 0 |
| B1p-2 | final | FILTERED_EMPTY, REPEATED_QUERY | 4 | 4 / 0 | 0/3 | 1 | 3 |
| B1p-3 | cancelled (context overflow) | FILTERED_EMPTY, REPEATED_QUERY | 4 | 4 / 0 | 0/3 | 3 | 3 |
| B1p-4 | final | FILTERED_EMPTY | 4 | 4 / 0 | 0/3 | 4 | 0 |

\*B0-4's medium-priority query returned only the coordinator's own sentinel. It then marked the sentinel completed and stopped.

The pattern is the same in every trial:

* **Identical opening.** The first three to four calls are the same filtered query for each role (and once for itself): `pending + high + sort`.
* **Two outcomes after empty results.** After four empty results the coordinator either:
  * **repeats** the query until the 80-turn limit (5/15 trials). Each loop cycles the owner filter through the roles: a period of 3 in B0-0 and 4 in the others, always with `priority="high"`. Or it
  * **creates duplicate tasks** with `task_create` (9/15 trials, 32 tasks) and then finishes. Its prompt explicitly says not to.
* **B0-4 does neither.** It finds only its own sentinel, marks it completed and stops.
* **No discovery.** The coordinator saw 0 of 3 seeded assignments in **15/15** trials.

**Looping vs non-looping trials.** They differ only in what happens *after* the empty results: looping versus `task_create` and a final answer. Both start the same way, and neither ever discovers the board. Condition does not separate them cleanly (B0 3/5, B1n 0/5, B1p 2/5 loops). With n=5, that difference is not interpretable.

**Messages.** In the 4 trials with sends, all 12 messages were delivered (`is_error=false`). They arrived at or after the end of the specialists' sessions, and 0/45 specialist sessions received a coordinator message. This is late coordination, not message loss.

## 5. Candidate causes

| # | Suspected cause | Evidence for | Evidence against | Remaining unknowns | Discriminating test | Smallest repair |
|---|---|---|---|---|---|---|
| H1 | Task-board implementation defect | – | Live matrix 13/13 equals ground truth on both servers; priority is the documented default | none | done (`test-task-board`) | none — rejected |
| H2 | Inaccurate or undiscoverable schema | – | Optional filters, enum values, the "only team_name lists every task" text, and empty-result semantics are all published and forwarded by Qwen Code; the prompt's step 1 gives the unfiltered call verbatim; R3 guidance repeats it | – | done (schema capture) | none — rejected as a defect |
| H3 | Side-effect of R4's published enum values | 0/5 pre-R4 coordinator sessions (V1R–V1R3) used `priority`; 15/15 R4 sessions did; R3→R4 prompt diffs are empty, so only the schema changed | Not randomized; a different run date; the schema is accurate | Whether the published `priority` enum *causes* the filter | **T2**: R4 vs vendored schema, same prompts | Not a defect. Hiding accepted values would reintroduce the R3 defect. PI decision only if T2 shows a schema effect |
| H4 | Urgency wording cues `high` | Only "high" token in prompts is "high-beta"; task says "immediate", "before market close today" | Identical wording in V1R–V1R3 produced no priority filter | Interaction with H3 | **T2**: original vs neutral wording (3 phrases edited) | None: the task text is protected |
| H5 | Model capability (Qwen3-8B) | Identical first call 15/15 at temperature 0.7; 483 repeats of a filter that always returned `[]`; ignored the explicit unfiltered example, the tool description and `board_team`; created duplicate tasks against instructions | Not separable from H3/H4 without T2 | Whether a larger model behaves differently | T2 (and, if approved, the same T2 with another local model) | Separately versioned model comparison (PI) |
| H6 | Orchestration | Specialists do not need the coordinator to *find* work: 45/45 retrieved their own assignment directly. But the coordinator never directs or sequences them (0/45 received a message) | – | – | T3 (specialists alone) | Out of scope without PI (sequencing changes the research question) |
| H7 | Team-name copying | 38 specialist calls (and some coordinator calls) dropped "8B" from the team name and got `Team not found` | All were followed by successful calls with the correct name | Whether a model-free team name removes these errors | T2/T3 record `wrong_team_name_calls` | PI option: team name without the model slug (orchestration change, new identity) |

## 6. Decision

Hierarchy §9: **A is rejected** (no defect). **B applies:** the contract is accurate, so no defect is invented. A controlled read-only capability test, T2, is implemented with the requested synthetic board: three medium-priority assignments plus the sentinel, no specialists and no answers. It is ready for manual launch. **C is likely**, but it cannot be separated from H3 until T2 runs.

No new model-visible tool, prompt change or result-level hint was implemented or activated. Options and exact proposals are in [BACH_COORDINATION_REPAIR_IMPLEMENTATION.md](BACH_COORDINATION_REPAIR_IMPLEMENTATION.md).
