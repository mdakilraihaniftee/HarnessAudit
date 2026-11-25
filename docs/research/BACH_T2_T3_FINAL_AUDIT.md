# BACH — T2/T3 final audit

Labels used: **[Evidence]** verified from raw artifacts · **[Implementation]** code change in this cycle · **[Model]** real Qwen3-8B observation · **[Inference]** reasoning from evidence · **[Hypothesis]** untested · **[PI]** decision needed.

Metric-by-metric numbers: [BACH_T2_T3_INDEPENDENT_AUDIT.md](BACH_T2_T3_INDEPENDENT_AUDIT.md) (24/24 reported values reproduced, 0 mismatches).

## 1. What ran

**[Evidence]**

* **T2:** 20 real coordinator sessions in 4 arms, 5 each. 20/20 observed, 20/20 final responses, 302 model requests, 2 context overflows.
* **T3:** 15 real specialist sessions, 5 per role. 15/15 final responses, 101 model requests, 0 HTTP errors.
* **Both runs:** fresh folders, no retries, owned server cleaned up and GPUs released. Model, sampling, limits and target identity unchanged.

## 2. Defects in the diagnostics themselves (my responsibility)

**D1 — T3 (and T2 boards) omitted the protected task context.** **[Evidence]**

* **Cause.** The isolated runner seeded assignments with ClawTeam's vanilla `seed_spoke_tasks`. The real TARGET_V1R4 harness patches that builder (`research_phase1r.target.assignment_description`): it qualifies tool names and appends *"Protected task context: <goal> {client_id, portfolio_id}"*.
* **Size of the difference.**
  * R4 trader assignment: 3,415 characters, with the IDs and goal.
  * T3 trader assignment: 2,680 characters, with neither.
* **Effect.** Specialists' user prompts carry no goal or IDs (in R4 these arrive only through the assignment). So in T3:
  * no specialist ever saw the client or portfolio, or what the task was (0/15);
  * risk officers and compliance analysts invented identifiers;
  * traders made zero finance calls and marked their task completed (5/5).
* **T2 is less affected.** The coordinator prompt is exact and contains the goal and team. Discovery is measured from task IDs in tool results, which is unaffected. Only the assignment *text* returned to the coordinator differed from R4.
* **Fixed in T3C;** see [BACH_T3_CONTEXT_REPAIR.md](BACH_T3_CONTEXT_REPAIR.md).

**D2 — The T2 "neutral" wording changed financial meaning.** **[Evidence]**

* Of the three edits, `reduce high-beta positions` → `reduce technology positions` changes the investment objective.
* The semantic-invariance check fails for that transformation: financial terms are not preserved.
* So the neutral arms are not a clean urgency comparison. A corrected urgency-only transformation (2 edits) passes all checks.
* See [BACH_T2_CORRECTED_INTERPRETATION.md](BACH_T2_CORRECTED_INTERPRETATION.md).

**D3 — Environment race in the runner.** **[Implementation]**

* **Cause.** Board seeding briefly patches `os.environ`. A concurrent subprocess spawn that inherited the environment could fail with `OSError: [Errno 14] Bad address`. This was reproduced 1 in 18 stub sessions.
* **Fix.** Every subprocess now receives an explicit environment snapshot taken under the seeding lock. Afterwards, 30/30 stub sessions completed cleanly, and a regression test was added.
* **Impact on the real runs:** none. All 35 sessions finished, and each MCP entry sets its data directory explicitly. The audit confirms that every session saw only its own seeded IDs.

## 3. What T2 establishes **[Model]**

* **First-call filter by schema:** with the R4 schema, the first `task_list` used `priority="high"` in 8/10 sessions (Wilson 95% 0.49–0.94). With the R3 schema: 0/10 (0–0.28).
* **Discovery of all three assignments:** R4 3/10; R3 4/10. Wilson intervals overlap almost entirely (0.11–0.60 vs 0.17–0.69).
* **Discovery via any permitted tool equals discovery via `task_list` in every arm.** `board_team` was used only with the R3 schema (4/10 sessions). In each such session it returned all three IDs, and each of those sessions also saw all three through `task_list`.
* **Discovery is possible.** 7/20 sessions saw all three assignments, and 6/20 referenced all three afterwards. So Qwen3-8B is not categorically unable to discover the board.
* **Duplicate tasks:** 7/20 sessions created tasks with `task_create` instead of using the seeded ones.

## 4. What T3 establishes

* **[Evidence]** Nothing about specialist capability. The diagnostic gave the agents no legitimate task context.
* **[Inference]** The zero committed effects and zero out-of-role actions are consequences of missing inputs. They are not evidence of incompetence or of role discipline.

## 5. What remains open

* **[Hypothesis]** Publishing the `priority` enum (R4) induces a high-priority first filter. This is associated (8/10 vs 0/10), but its effect on *discovery* is not visible at n=10.
* **[Hypothesis]** Urgency framing drives the `high` choice. This is untested: the old neutral arms are confounded.
* **[Open]** Specialist capability with correct context and prerequisites. That is what T3C is for.

## 6. Decisions **[PI]**

Run T3C: a smoke first, then 15 sessions. The optional urgency-only T2 follow-up is prepared but not required for the next decision. See [BACH_TARGET_CAPABILITY_DECISION.md](BACH_TARGET_CAPABILITY_DECISION.md).
