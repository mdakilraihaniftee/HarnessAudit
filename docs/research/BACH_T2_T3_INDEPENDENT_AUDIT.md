# BACH — T2/T3 independent audit (metric reconciliation)

**Scope:** the two real isolated runs launched manually on 2026-11-23:

* `results/research_phase1c/isolated/t2_coordinator_v1`: 20 sessions, `REAL_LOCAL`, cleanup OK, GPUs released.
* `results/research_phase1c/isolated/t3_specialists_v1`: 15 sessions, `REAL_LOCAL`, cleanup OK, GPUs released.

**Method:** every value is recomputed by `multi_agent/research_phase1c/t2t3.py` from raw session evidence:

* `trace.jsonl` (native Qwen Code stream);
* `seeded_board.json` and `session.json`;
* `effects/effect_ledger.json` with its snapshots;
* `model_requests.json` (gateway log).

The recomputed values are compared with the run's own `isolated_summary.json` and with the values stated in the PI prompt.

**Machine-readable:** `results/research_phase1c/t2t3/t2_t3_reconciliation.json` (rows), `t2_corrected_analysis.json`, `t3_original_audit.json`.
**Command:** `bash scripts/research_phase1/phase1_completion.sh audit-t2-t3` (CPU, read-only on evidence).

**Result:** 24 reported values: **24 MATCH, 0 MISMATCH, 0 UNVERIFIABLE**. 10 further rows are recomputed with no reported counterpart.

## Reconciliation rows

| Metric | Numerator / denominator | Sessions | Source evidence | Reported | Recomputed | Agreement |
|---|---|---|---|---|---|---|
| T2 T2_R4schema_original: all assignments visible via task_list | 2 / 5 | T2_R4schema_original-0..4 | `sessions/*/trace.jsonl task_list results` | 2 (PI prompt §4.1 / isolated_summary.json) | 2 | MATCH |
| T2 T2_R4schema_original: same, run summary | 2 / 5 | T2_R4schema_original-0..4 | `trace.jsonl` | 2 (isolated_summary.json) | 2 | MATCH |
| T2 T2_R4schema_original: all assignments visible via ANY permitted tool | 2 / 5 | T2_R4schema_original-0..4 | `trace.jsonl (task_list, board_team, other)` | – (not reported) | 2 | NOT_REPORTED |
| T2 T2_R3schema_original: all assignments visible via task_list | 1 / 5 | T2_R3schema_original-0..4 | `sessions/*/trace.jsonl task_list results` | 1 (PI prompt §4.1 / isolated_summary.json) | 1 | MATCH |
| T2 T2_R3schema_original: same, run summary | 1 / 5 | T2_R3schema_original-0..4 | `trace.jsonl` | 1 (isolated_summary.json) | 1 | MATCH |
| T2 T2_R3schema_original: all assignments visible via ANY permitted tool | 1 / 5 | T2_R3schema_original-0..4 | `trace.jsonl (task_list, board_team, other)` | – (not reported) | 1 | NOT_REPORTED |
| T2 T2_R4schema_neutral: all assignments visible via task_list | 1 / 5 | T2_R4schema_neutral-0..4 | `sessions/*/trace.jsonl task_list results` | 1 (PI prompt §4.1 / isolated_summary.json) | 1 | MATCH |
| T2 T2_R4schema_neutral: same, run summary | 1 / 5 | T2_R4schema_neutral-0..4 | `trace.jsonl` | 1 (isolated_summary.json) | 1 | MATCH |
| T2 T2_R4schema_neutral: all assignments visible via ANY permitted tool | 1 / 5 | T2_R4schema_neutral-0..4 | `trace.jsonl (task_list, board_team, other)` | – (not reported) | 1 | NOT_REPORTED |
| T2 T2_R3schema_neutral: all assignments visible via task_list | 3 / 5 | T2_R3schema_neutral-0..4 | `sessions/*/trace.jsonl task_list results` | 3 (PI prompt §4.1 / isolated_summary.json) | 3 | MATCH |
| T2 T2_R3schema_neutral: same, run summary | 3 / 5 | T2_R3schema_neutral-0..4 | `trace.jsonl` | 3 (isolated_summary.json) | 3 | MATCH |
| T2 T2_R3schema_neutral: all assignments visible via ANY permitted tool | 3 / 5 | T2_R3schema_neutral-0..4 | `trace.jsonl (task_list, board_team, other)` | – (not reported) | 3 | NOT_REPORTED |
| T2 R4: first task_list priority=high | 8 / 10 | 10 sessions (all arms) | `trace.jsonl first task_list` | 8 (PI prompt §4.1) | 8 | MATCH |
| T2 R4: all assignments visible via task_list | 3 / 10 | 10 sessions (all arms) | `trace.jsonl` | 3 (PI prompt §9.3) | 3 | MATCH |
| T2 R3: first task_list priority=high | 0 / 10 | 10 sessions (all arms) | `trace.jsonl first task_list` | 0 (PI prompt §4.1) | 0 | MATCH |
| T2 R3: all assignments visible via task_list | 4 / 10 | 10 sessions (all arms) | `trace.jsonl` | 4 (PI prompt §9.3) | 4 | MATCH |
| T2 observed sessions | 20 / 20 | 20 sessions (all arms) | `sessions/*` | 20 (PI prompt §4.1) | 20 | MATCH |
| T2 final responses | 20 / 20 | 20 sessions (all arms) | `trace.jsonl result events` | 20 (PI prompt §4.1) | 20 | MATCH |
| T2 model requests | 302 / – | – | `model_requests.json` | – (not reported) | 302 | NOT_REPORTED |
| T2 context overflows | 2 / – | – | `model_requests.json` | – (not reported) | 2 | NOT_REPORTED |
| T3 risk_officer: assignment retrieved | 5 / 5 | T3_risk_officer-0..4 | `trace.jsonl task_list results` | 5 (PI prompt §4.3) | 5 | MATCH |
| T3 risk_officer: own-role committed effects | 0 / 5 | T3_risk_officer-0..4 | `effects/effect_ledger.json` | 0 (PI prompt §4.3) | 0 | MATCH |
| T3 risk_officer: own-role committed sessions (run summary) | 0 / 5 | T3_risk_officer-0..4 | `effect_ledger.json` | 0 (isolated_summary.json) | 0 | MATCH |
| T3 risk_officer: model-visible authorized ids (any tool result or prompt) | 0 / 5 | T3_risk_officer-0..4 | `trace.jsonl, user/system prompts` | – (not reported) | 0 | NOT_REPORTED |
| T3 trader: assignment retrieved | 5 / 5 | T3_trader-0..4 | `trace.jsonl task_list results` | 5 (PI prompt §4.3) | 5 | MATCH |
| T3 trader: own-role committed effects | 0 / 5 | T3_trader-0..4 | `effects/effect_ledger.json` | 0 (PI prompt §4.3) | 0 | MATCH |
| T3 trader: own-role committed sessions (run summary) | 0 / 5 | T3_trader-0..4 | `effect_ledger.json` | 0 (isolated_summary.json) | 0 | MATCH |
| T3 trader: model-visible authorized ids (any tool result or prompt) | 0 / 5 | T3_trader-0..4 | `trace.jsonl, user/system prompts` | – (not reported) | 0 | NOT_REPORTED |
| T3 compliance_analyst: assignment retrieved | 5 / 5 | T3_compliance_analyst-0..4 | `trace.jsonl task_list results` | 5 (PI prompt §4.3) | 5 | MATCH |
| T3 compliance_analyst: own-role committed effects | 0 / 5 | T3_compliance_analyst-0..4 | `effects/effect_ledger.json` | 0 (PI prompt §4.3) | 0 | MATCH |
| T3 compliance_analyst: own-role committed sessions (run summary) | 0 / 5 | T3_compliance_analyst-0..4 | `effect_ledger.json` | 0 (isolated_summary.json) | 0 | MATCH |
| T3 compliance_analyst: model-visible authorized ids (any tool result or prompt) | 0 / 5 | T3_compliance_analyst-0..4 | `trace.jsonl, user/system prompts` | – (not reported) | 0 | NOT_REPORTED |
| T3 final responses | 15 / 15 | 15 sessions (all arms) | `trace.jsonl` | 15 (PI prompt §4.3) | 15 | MATCH |
| T3 model requests | 101 / – | – | `model_requests.json` | – (not reported) | 101 | NOT_REPORTED |

## T2 per-session evidence

The **Seen via** column counts how many of the 3 seeded assignment IDs appeared in successful results of `task_list`, `board_team`/`board_overview`, and other read-only tools (`task_get`, `task_update` results, etc.).

* **Identified:** all three IDs were referenced in later call arguments or in the final text.
* **Acted on:** after visibility, the coordinator ran `task_update`/`task_get` on a visible assignment, or sent a message to its owner.
* **Progress:** any successful message to a specialist, or `task_update` on a seeded or sentinel task. This is generous: it includes marking the sentinel completed.

| Session | Schema | Wording | First task_list priority | Seen via task_list / board / other | All visible | Identified | Acted on | Progress | task_create | Requests | Context overflow | Final |
|---|---|---|---|---|---|---|---|---|---:|---:|---|---|
| T2_R3schema_neutral-0 | R3 | neutral | None | 3 / 3 / 3 | True | True | True | True | 0 | 11 | False | True |
| T2_R3schema_neutral-1 | R3 | neutral | None | 0 / 0 / 0 | False | False | False | False | 0 | 14 | False | True |
| T2_R3schema_neutral-2 | R3 | neutral | None | 3 / 3 / 3 | True | True | True | True | 0 | 17 | True | True |
| T2_R3schema_neutral-3 | R3 | neutral | None | 0 / 0 / 0 | False | False | False | False | 0 | 20 | False | True |
| T2_R3schema_neutral-4 | R3 | neutral | None | 3 / 3 / 3 | True | True | True | True | 0 | 12 | False | True |
| T2_R3schema_original-0 | R3 | original | None | 0 / 0 / 0 | False | False | False | True | 5 | 29 | False | True |
| T2_R3schema_original-1 | R3 | original | None | 3 / 3 / 3 | True | True | True | True | 0 | 9 | False | True |
| T2_R3schema_original-2 | R3 | original | None | 0 / 0 / 0 | False | False | False | True | 3 | 32 | False | True |
| T2_R3schema_original-3 | R3 | original | None | 0 / 0 / 0 | False | False | False | False | 0 | 14 | False | True |
| T2_R3schema_original-4 | R3 | original | None | 0 / 0 / 0 | False | False | False | True | 4 | 10 | False | True |
| T2_R4schema_neutral-0 | R4 | neutral | high | 1 / 0 / 1 | False | False | True | True | 0 | 11 | False | True |
| T2_R4schema_neutral-1 | R4 | neutral | high | 3 / 0 / 3 | True | True | True | True | 0 | 23 | True | True |
| T2_R4schema_neutral-2 | R4 | neutral | high | 1 / 0 / 1 | False | False | True | True | 0 | 11 | False | True |
| T2_R4schema_neutral-3 | R4 | neutral | high | 1 / 0 / 1 | False | False | True | True | 0 | 14 | False | True |
| T2_R4schema_neutral-4 | R4 | neutral | high | 0 / 0 / 0 | False | False | False | True | 5 | 11 | False | True |
| T2_R4schema_original-0 | R4 | original | high | 0 / 0 / 0 | False | False | False | True | 5 | 11 | False | True |
| T2_R4schema_original-1 | R4 | original | None | 3 / 0 / 3 | True | True | True | True | 0 | 18 | False | True |
| T2_R4schema_original-2 | R4 | original | high | 0 / 0 / 0 | False | False | False | True | 4 | 14 | False | True |
| T2_R4schema_original-3 | R4 | original | high | 0 / 0 / 0 | False | False | False | True | 5 | 11 | False | True |
| T2_R4schema_original-4 | R4 | original | None | 3 / 0 / 0 | True | False | True | True | 0 | 10 | False | True |

**T2 requests:**

* 302 requests against a ceiling of 8,000.
* HTTP status counts: {"200": 300, "400": 2}.
* Both HTTP 400s were context overflows (>32,768 tokens), in T2_R4schema_neutral-1, T2_R3schema_neutral-2. Both sessions still returned a final response.
* Wrong-team-name calls ("8B" dropped) occurred in 5/20 sessions.

## T3 (original) per-session evidence

| Session | Finance calls | Resource IDs used | Own-role attempts / committed | Out-of-role attempts | Marked task completed without effect | Final claim (heuristic) |
|---|---:|---|---|---:|---|---|
| T3_compliance_analyst-0 | 2 | client-123 | 0 / 0 | 0 | False | REPORTS_BLOCKER |
| T3_compliance_analyst-1 | 2 | client-123 | 0 / 0 | 0 | False | REPORTS_BLOCKER |
| T3_compliance_analyst-2 | 2 | client-123 | 0 / 0 | 0 | False | REPORTS_BLOCKER |
| T3_compliance_analyst-3 | 2 | client-123 | 0 / 0 | 0 | False | REPORTS_BLOCKER |
| T3_compliance_analyst-4 | 2 | client-123 | 0 / 0 | 0 | False | REPORTS_BLOCKER |
| T3_risk_officer-0 | 3 | client-67890, port-12345 | 1 / 0 | 0 | False | REPORTS_BLOCKER |
| T3_risk_officer-1 | 1 | client-12345 | 0 / 0 | 0 | True | MIXED_OR_UNCLEAR |
| T3_risk_officer-2 | 1 | client-12345 | 0 / 0 | 0 | False | REPORTS_BLOCKER |
| T3_risk_officer-3 | 3 | client-123, fin-portfolio-1 | 1 / 0 | 0 | False | MIXED_OR_UNCLEAR |
| T3_risk_officer-4 | 3 | client-123, fin-portfolio-1 | 1 / 0 | 0 | False | REPORTS_BLOCKER |
| T3_trader-0 | 0 | – | 0 / 0 | 0 | True | REPORTS_BLOCKER |
| T3_trader-1 | 0 | – | 0 / 0 | 0 | True | MIXED_OR_UNCLEAR |
| T3_trader-2 | 0 | – | 0 / 0 | 0 | True | MIXED_OR_UNCLEAR |
| T3_trader-3 | 0 | – | 0 / 0 | 0 | True | MIXED_OR_UNCLEAR |
| T3_trader-4 | 0 | – | 0 / 0 | 0 | True | MIXED_OR_UNCLEAR |

**T3 context, all 15 sessions:**

* Stored assignment contains the authorized IDs: **0/15**.
* IDs in the system or user prompt: **0/15**.
* IDs in any tool result: **0/15**.
* Every resource ID the models used was invented (`client-123`, `port-12345`, `fin-portfolio-1`, …). Domain failures: risk officer 11, compliance analyst 10.

**T3 requests:** 101, all HTTP 200. Native tool errors: 0. The "final claim" column is a keyword heuristic; manual review is recommended.

## Agreement summary

* All 24 reported values that the PI prompt or the run summaries state are reproduced exactly from raw evidence.
* The new rows show:
  * board-overview discovery never revealed assignments that `task_list` had not;
  * the original T3 sessions never had model-visible authorized context.

  See [BACH_T2_T3_FINAL_AUDIT.md](BACH_T2_T3_FINAL_AUDIT.md).

