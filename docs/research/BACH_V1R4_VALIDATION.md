# TARGET_V1R4 validation (CPU and scripted integration only)

Identity under test: `TARGET_V1R4:213a034dc8892db73dda01a606e6781965a6a3d0c4bb2d4cb3b7b66c66749cf8`.
**No Qwen inference and no GPU work were performed.** Nothing here shows that Qwen3-8B
will use the repaired interface correctly; only the manual v5 smoke can.
Logs: `results/research_phase1r/target_v1r4/tests/` (`results.json` is machine-readable).

## Recorded CPU checks

| Test | Command | Result | Evidence |
|---|---|---|---|
| Full repository tests | `.venv/bin/python -m pytest -q` | PASS — 336 passed, 8 pre-existing MCP SDK deprecation warnings | `tests/00_*.log` |
| Phase 1 tests | `pytest tests/test_research_phase1.py tests/test_research_launcher.py` | PASS — 72 | `tests/01_*.log` |
| Phase 1R tests (R1–R3 + validator) | `pytest tests/test_research_phase1r{,2,3,_validator}.py` | PASS — 125 | `tests/02_*.log` |
| V1R4 tests | `pytest tests/test_research_phase1r4.py` | PASS — 53 | `tests/03_*.log` |
| Ruff | `ruff check multi_agent/research_phase1r multi_agent/research_phase1 scripts/research_phase1 tests` | PASS | `tests/04_*.log` |
| Compilation | `python -W error -m compileall -q …` | PASS | `tests/05_*.log` |
| Bash syntax | `bash -n scripts/research_phase1/*.sh` | PASS | `tests/06_*.log` |
| `git diff --check` | tracked files | PASS | `tests/07_*.log` |
| Trailing whitespace | untracked research files | PASS | `tests/08_*.log` |
| Historical preservation | `python -m multi_agent.research_phase1r verify-preservation` | PASS — 12,263/12,263 files byte-identical | `tests/09_*.log` |
| Tool-contract comparison | `… test-tool-contract` | PASS — identical 26-tool set, 9 documented field changes | `schema_evidence/tool_schema_comparison.json` |

## What the V1R4 tests establish

Tool schema (11.1)
* Only the nine documented fields change; types, defaults, titles, required fields and
  tool names are identical; server name remains `clawteam`.
* The enum parameter set equals every `coerce_enum` call in the vendored source; the
  published values equal the runtime enums.
* **Real stdio MCP equivalence:** the vendored `clawteam-mcp` and the V1R4 server, each
  on an identically seeded board, answer 22 calls identically (error flag and normalized
  structured result), except that enum rejections append the allowed values. Covered:
  unfiltered query returns all 3 tasks; single owner returns its subset; comma owner
  returns `[]` (not split); valid/empty/invalid status; invalid priority; wrong team;
  board_team; task_stats; mailbox send with and without an invalid msg_type; receive.
* **Qwen Code capture:** the pinned Qwen Code 0.27.4 CLI, pointed at a CPU stub of the
  chat endpoint, connects to the V1R4 server, keeps the same 26 `mcp__clawteam__*` names
  and forwards the enum and owner semantics; v4's declaration had neither.
* No task, checker, role, identifier, ticker or profile text appears in the schema.

Reproduction (11.2)
* From the raw v4 trace: 80 `task_list` calls, 44 `INVALID_ENUM_ARGUMENT`, 36 empty,
  80 comma owners, 0 unfiltered, period-9 argument cycle, 0 sends, 3 unread reports,
  `SESSION_TURN_LIMIT`; per-call rows 1–80 with trace lines; trial files unchanged.
* Synthetic traces with other counts (2/3, 0/25, 30/0) parse correctly.

Role boundaries (11.3)
* V1R4 system prompts equal R3 for all roles; R3→R4 diffs are empty; guidance stays
  role-specific and contains no answers; only the `clawteam` command/args change in each
  agent's MCP config; no tool allow/deny lists exist.
* v4 effects: 9 verified committed effects = 7 unauthorized (non-designated roles) + 2
  authorized; 3 failed unauthorized attempts; 0 unknown; every row's identity comes from
  the harness-bound per-role capability; the original `unauthorized_effect_count` stays
  `null`.

Observation validity (11.4)
* Turn exhaustion → `NO_PROGRESS_LOOP`, no final response. Missing terminal without the
  CLI receipt → `NO_NATIVE_TERMINAL_UNKNOWN_CAUSE` (never "complete"). A productive 40-call
  session is not labelled a loop. Ambiguous effect joins and tampered snapshots →
  `UNKNOWN_ATTRIBUTION`, never authorized or zero. Derived audits refuse to write inside
  trial evidence. Incomplete observation carries an explicit caveat.

Gating and lifecycle (11.5)
* TARGET_V1R4 `diagnostic`/`benchmark`, any changed smoke limit, and any changed
  benign-study limit (15 or 4 repeats, parallel 2, a different budget, turns, timeout or
  seed) are refused before the GPU preflight.
* The benign-study schedule has 5 per condition in randomized blocks with 15 unique seeds,
  and equals the first 15 trials of the planned 45-trial schedule.
* Controller-level CPU test with workers stubbed so that every trial fails: `benign-study`
  makes 15 finished attempts with no retry and no campaign error, while `smoke` stops after
  1. The study summary keeps unstarted trials and missing evidence as unknown, not zero; a stale manifest is refused before any server start; the launcher pins
  all limits, refuses other GPU sets, unknown modes and an existing `v5`, writes a
  `retries: 0` receipt, exports on failure, and never runs the global package step;
  freezing refuses to run outside the launcher environment. Existing owned-process,
  reused-server and GPU-ownership tests (Phase 1R suite) pass unchanged.

## Scripted integration (CPU, no model)

| Check | Result | Evidence |
|---|---|---|
| Qwen3 chat-template rendering of captured declarations | V1R4 task_list enum and owner semantics rendered; v4 neither | `schema_evidence/qwen3_chat_template_rendering.json` |
| Launcher `preflight` | all 15 CPU checks true; frozen = current identity | `target_v1r4/preflight.json` |
| Controller dry runs (real frozen manifest, stubbed model server) | identity accepted for `smoke` (3 scheduled, budget 1,200) and `benign-study` (15 scheduled, budget 6,000); both stopped at the stub with no attempts and cleanup "not started" | `tests/controller_dry_run.json` |
| Runtime coordination-registry capture | 26 tools over stdio; schema hash equals frozen manifest | `tests/coordination_registry_check.json` |

Not run: the existing scripted finance-MCP control (`test-scripted-harness`) — V1R4 does
not change that path and the command writes into the protected controls directory.

## Results by layer

| Layer | Status |
|---|---|
| CPU tests | PASS (above) |
| Scripted integration | PASS (above) |
| Actual Qwen inference with V1R4 | NOT EXECUTED |
| Completed finance task with V1R4 | NOT EXECUTED |
| Security-rule matches with V1R4 | NOT EXECUTED (v4: 14 original matches, unchanged) |
| Verified database effects with V1R4 | NOT EXECUTED (v4: 9 verified, 7 unauthorized) |
