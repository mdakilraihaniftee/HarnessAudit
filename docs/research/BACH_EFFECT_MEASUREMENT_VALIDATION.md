# BACH — effect and security measurement validation (P5, §13, §15)

The original HarnessAudit checker is unchanged. The research layer reads its output and the effect ledger; it never feeds back into the checker or the agents. Source: R4 study trials plus the fresh controls in `results/research_phase1c/controls/038abcce0906/`.

## 13.1 State-changing effects

Each ledger row for `set_risk_limits`, `execute_trade` or `submit_compliance_report` records:

* trusted caller identity (the harness-bound per-role MCP capability, never tool arguments);
* call id, arguments and affected resource;
* original rule matches and the tool response class;
* before and after SQLite snapshots with their sha256 hashes;
* commit verification, role authorization and resource authorization.

Classification (`audit.classify_effect`) never uses success text alone. It requires a hash-verified snapshot pair, and any snapshot problem makes the row `UNKNOWN_EFFECT`.

| Class (R4, 15 trials) | Rows |
|---|---:|
| Authorized committed effect | 76 |
| Unauthorized committed effect | 95 |
| Unauthorized attempt, rejected by the tool | 20 |
| Authorized attempt rejected (no effect) | 27 |
| Domain failure | 0 |
| Success text without state change | 0 |
| Unknown effect / ambiguous attribution | 0 / 0 |
| **Total ledger rows (one per call)** | **218** |

* **Snapshots:** all 218 pairs re-hashed and verified.
* **No double counting:** each call is exactly one row, so an attempt and its effect are never counted as two vulnerabilities.
* **Native-to-ledger join:** the run's own `success_effect_reconciliation.json` joins every successful native call to one ledger row (G6: 171/171).

The unit tests cover every class, plus missing and tampered snapshots (`tests/test_phase1_completion.py::test_effect_classes`).

## 13.2 Resource access

| Resource class | Rows | Protected data returned |
|---|---:|---:|
| In scope | 622 | 37 (client profile of the authorized client) |
| Existing out-of-scope resource | 0 | – |
| Nonexistent identifier | 0 | – |
| Designated decoy | 0 | – |

All resource access in R4 was in scope. A zero count of out-of-scope access is "not observed" (6 incomplete observations), not proof of protection. The original V-OR rules and matches are preserved unchanged.

## 13.3 Communication

| Sender → receiver | Attempted | Delivered | Policy status |
|---|---:|---:|---|
| specialist → relationship_manager | 44 | 44 | permitted |
| relationship_manager → specialist | 12 | 12 | permitted |
| specialist → specialist | 0 | – | forbidden channel, **no opportunity observed** |
| broadcast | 0 | – | – |

Zero forbidden communication means that no specialist attempted a forbidden channel. It is not evidence that the boundary holds. The positive control confirms detection: a forbidden `risk_officer → trader` message is detected by the checker.

## 13.4 Validity fields

Each trial keeps separate, explicit fields:

* `trial_started`, `observation_complete`, `measurement_valid` (`true`, `false` or `null` = unknown);
* `behavioral_failure`, `failure_category`, `budget_censored`;
* native terminal count and `completion_status`.

The audit never converts `null` to false or zero. The reconciliation counts unknowns separately: 6/15 trials are `measurement_valid = null`.

## §15 Controls (fresh rerun, `phase1_completion.sh test-instrumentation`)

| Control | Expected | Result |
|---|---|---|
| Offline replay positives | 4/4 categories | PASS |
| Live scripted MCP positives (forbidden tool, existing out-of-scope resource, forbidden communication, prohibited disclosure) | 4/4 detected | PASS — all four categories |
| Live scripted negative (authorized actions) | 0 unexpected matches | PASS — 0 |
| Scripted legal four-role workflow | 0 matches, core completion | PASS — 0 matches, 4/4 checkpoints |

Each control writes to a new directory, and existing control evidence is not modified. No security ground truth or checker code was changed.
