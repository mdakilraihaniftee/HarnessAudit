# BACH — T2 corrected interpretation (isolated coordinator)

**Source:** `results/research_phase1c/t2t3/t2_corrected_analysis.json`, recomputed from the 20 raw traces (`analyze-t2`). All counts are **descriptive** (n=5 per arm, 10 per schema). Intervals are Wilson 95%. No causal or superiority claim is made.

## 1. Separate discovery indicators **[Evidence, Model]**

| Indicator (sessions) | R4 schema (n=10) | R3 schema (n=10) |
|---|---|---|
| First `task_list` uses `priority="high"` | **8** (0.49–0.94) | **0** (0.00–0.28) |
| Any valid unfiltered `task_list` | 2 | 0 |
| All 3 assignments visible via `task_list` | 3 (0.11–0.60) | 4 (0.17–0.69) |
| …via `board_team` / `board_overview` | 0 (never called) | 4 sessions called it; each saw all 3, and each of those sessions also saw all 3 via `task_list` |
| …via any permitted tool | 3 | 4 |
| All 3 assignments identified (referenced later) | 2 | 4 |
| Relevant assignment acted on (update / get / message to its owner) | 6 | 4 |
| Downstream progress (message to a specialist, or task/sentinel update) | 10 | 7 |
| Created duplicate tasks (`task_create`) | 4 | 3 |
| Context overflow | 1 | 1 |
| Final response | 10 | 10 |

Per arm, visible via `task_list` / via any tool: R4-original 2/2, R3-original 1/1, R4-neutral 1/1, R3-neutral 3/3.

**Correction of the earlier metric.** Counting `task_list` only did not undercount: board-overview visibility never added a session. Visibility and use are now separate. For example, R4-original-4 saw all three assignments but did not reference all three afterwards. "Progress" is generous by construction: it includes marking the sentinel completed, which some sessions did prematurely.

## 2. Wording confound **[Evidence]**

* **Old neutral arms are not clean.** Their transformation replaced "high-beta" with "technology", which changes the financial objective. `semantic_invariance` → `financial_terms_preserved: false`. They must not be read as an urgency experiment.
* **Corrected urgency-only transformation** (`URGENCY_EDITS`; `isolated.render(..., "urgency_neutral")`). It makes exactly two edits:
  * "has requested an immediate downgrade" → "has requested a downgrade";
  * "Before market close today, the team must:" → "The team must:".
* **Automated invariance checks, all passing (tested):**
  * identifiers (CL-7392, PF-4821) preserved;
  * roles preserved;
  * tool names preserved;
  * financial terms preserved by count: Moderate-Aggressive, Conservative, NVDA, high-beta, tech, risk_tolerance, the three limit fields, suitability, memo, lower, reduce, sell;
  * the only remaining difference is the two deletions;
  * the urgency words are gone.
* **Human-review checklist** for a reviewer, before any claim about urgency:
  1. Is the client's requested risk direction unchanged (toward Conservative)?
  2. Are the five numbered obligations identical?
  3. Are the assets to reduce identical (NVDA, other tech, *high-beta* kept)?
  4. Is any deadline or obligation removed that a specialist would need? (Only "before market close today" is removed. It is a time constraint, so state it as a limitation.)
  5. Are roles, tool descriptions and the team/sentinel instructions unchanged?
* **Limitation.** The token "high" remains, inside "high-beta". The urgency-only arm cannot separate a lexical "high" cue from urgency framing. Removing "high-beta" would change the objective and is not allowed.

## 3. What can and cannot be said

* **Established (descriptive).** With the R4 schema the first query usually carries `priority="high"`; with R3 it never did. Assignment discovery is low and similar under both schemas (3/10 vs 4/10). Qwen3-8B *can* discover the board (7/20 sessions), but does so unreliably.
* **Hypothesis only.** That publishing the enum *causes* the high filter. Even if it does, removing it does not improve discovery at this sample size.
* **Not established.** Any urgency effect, because the old arms are confounded.

## 4. Optional follow-up (prepared, not recommended for the next decision)

`RUN_REAL=YES … t2-followup` runs the following design:

* **One factor:** wording (original vs urgency-only).
* **Held fixed:** schema R4 and the corrected R4 assignment builder.
* **Size:** n=10 per arm, pre-declared; 20 sessions in total, at most 8,000 requests.
* **Primary metric:** first-call `priority="high"`. **Secondary:** all-assignments-visible.
* **Run rules:** fresh folder, frozen seeds and configuration, no replacement of failures.

It answers only the urgency question. Because discovery is poor under both schemas, its result would not change the next PI decision. That decision depends on T3C.
