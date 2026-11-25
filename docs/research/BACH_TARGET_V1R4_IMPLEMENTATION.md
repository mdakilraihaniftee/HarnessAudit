# TARGET_V1R4 — truthful ClawTeam tool contract (Level 1 repair)

Frozen identity: `TARGET_V1R4:213a034dc8892db73dda01a606e6781965a6a3d0c4bb2d4cb3b7b66c66749cf8`
(`results/research_phase1r/target_v1r4/TARGET_V1R4.json`, 160 bound source files).
**No V1R4 inference has been run.** Evidence for the defect:
`BACH_R3_INDEPENDENT_ROOT_CAUSE.md`.

## The one repair

The vendored ClawTeam MCP server exposes enum-coerced and exact-match arguments as
undocumented `string | null`. V1R4 serves the **same tool functions**, under the **same
MCP server name** `clawteam` (so every model-visible name stays `mcp__clawteam__<tool>`),
from a versioned adapter, `multi_agent/research_phase1r/tool_contract.py`. The vendored
library is not modified. Exactly nine schema fields change:

| Tool | Field | Before (v4) | After (V1R4) |
|---|---|---|---|
| task_list | description | "List tasks for a team with optional filters." | filters optional, AND-combined; call with only `team_name` to list every task; empty list = no task matched all filters, not necessarily an empty board |
| task_list | owner | `string\|null` | same type + "exactly one owner name … compared as one whole, exact string. Several names or a comma-separated list are not split and normally match no task. Omit owner to include tasks of every owner." |
| task_list | status | `string\|null` | `enum [pending, in_progress, completed, blocked] \| null` + omit-to-include-all text |
| task_list | priority | `string\|null` | `enum [low, medium, high, urgent] \| null` |
| task_create | priority | `string\|null` | `enum [low, medium, high, urgent] \| null` |
| task_update | status | `string\|null` | `enum [pending, in_progress, completed, blocked] \| null` |
| task_update | priority | `string\|null` | `enum [low, medium, high, urgent] \| null` |
| mailbox_send | msg_type | `string\|null` | `enum` of the 12 `MessageType` values `\| null`; omit for an ordinary message |
| mailbox_broadcast | msg_type | `string\|null` | same enum; omit for an ordinary broadcast |

Full before/after schemas: `target_v1r4/schema_evidence/tool_schema_comparison.json`;
as forwarded by Qwen Code: `qwen_model_visible_clawteam_tools.json`; as rendered into the
Qwen3 prompt: `qwen3_chat_template_rendering.json` (task_list status enum and owner
semantics present in V1R4, absent in v4; +538 tokens across the 26 declarations).

The enum lists are generated from the runtime enums (`TaskStatus`, `TaskPriority`,
`MessageType`), and a test derives the parameter set from every `coerce_enum(...)` call in
the vendored tool source, so schema and runtime cannot silently drift. Enum parameters
were included because the same hidden contract produced rejections in v1–v4
(`waiting_for_input`, `urgent`, `task_update`, `report`, and v4's 44 status values).

**Error text.** For a value outside an enum, the original message is kept verbatim and
the accepted values are appended, e.g. `'all' is not a valid TaskStatus. Allowed status
values: pending, in_progress, completed, blocked. Omit status to apply no status value.`

### What does not change (tested)

* Accept/reject decisions: the pre-check uses the same truthiness test and `Enum(value)`
  as `coerce_enum`; `status=""` is still accepted as "no filter". Invalid values are never
  rewritten, aliased or accepted.
* Returned payloads: identical for every valid call, including the `{"result": …}`
  structured-output envelope (the vendored signature object, with its string return
  annotations, is reused; evaluating it would have changed the envelope — caught and
  avoided during development).
* Tool set (26 tools), argument names, types, defaults, required fields, permissions,
  data directory and the finance MCP server.
* A comma-separated owner still returns `[]`; it is documented, not interpreted.
* No task, checker, role, identifier, ticker, profile or numeric information appears in
  any schema text (test).

### Runtime wiring

`RepairedRuntime._prepare_agent` (TARGET_V1R4 only) replaces the per-agent `clawteam`
MCP entry's `command/args` with `<venv python> -m multi_agent.research_phase1r.tool_contract`
and adds `PYTHONPATH`; `CLAWTEAM_DATA_DIR`, `trust`, `timeout` and the finance entry are
unchanged. Each V1R4 trial records the served registry in
`instrumentation/coordination_tool_registry.json` (stdio `tools/list`, schema hash); a
CPU check confirms that hash equals the frozen manifest's.

## Prompts

None changed. V1R4 system prompts are exactly the TARGET_V1R3 guidance for all four
roles; user prompts and assignment descriptions are the original renderings. The
per-role R3→R4 system-prompt diffs saved in `target_v1r4/prompt_diffs/` are empty, and
effective system-prompt hashes in TARGET_V1R4.json equal TARGET_V1R3's.

## Observation-only additions (no effect on runs or acceptance)

* `coordination_audit.py`: per-call ledgers, refined coordination error taxonomy
  (`INVALID_ENUM_ARGUMENT`, …; the shared `audit.error_taxonomy` used for validity is
  unchanged), specialist timelines and the no-progress diagnostic (repeated queries,
  invalid arguments, distinct board states, sends, progress events, final response,
  stop reason; label `NO_PROGRESS_LOOP` when there is no final response and the last ≥20
  calls brought no new information or communication). It never rewrites calls, injects
  actions, stops a trial or marks success. For V1R4 trials the worker writes it to
  `<campaign>/derived/<trial>/`, outside the trial evidence folder.
* `effect_attribution.py`: derived research report linking rule matches to verified
  committed effects (see root-cause §5). `research_security.json` and the checker are
  untouched.

## Identity and gating

TARGET_V1R4.json binds everything TARGET_V1R3 bound plus the adapter, audit modules,
launcher, exporter, validator and tests, and a `coordination_tool_contract` block (tool
names, changed fields, model-visible schema sha256
`05aad824…a1c871`, `vendored_clawteam_modified=false`, `permissions_changed=false`).
Task, checker, rule catalog, FinanceBank, fixtures, role identities, Qwen3-8B revision,
temperature 0.7, thinking off, context 32,768, 80 native turns, 300 s timeout, request
ceilings (1,200 / 400) and seed policy equal TARGET_V1R3's (test).
The controller permits exactly two TARGET_V1R4 commands, and refuses anything else
before any GPU or resource action:

* `smoke`: the frozen smoke limits; stops at the first trial without valid measurement.
* `benign-study` (user-approved 2026-11-23): 5 randomized blocks of B0/B1n/B1p
  (15 trials), the same per-trial limits, a total request ceiling of 6,000, no early stop,
  no retries, no replacement and no smoke prerequisite. It reuses the existing scheduler
  with a condition filter, so its trial IDs and seeds equal the first 15 trials of the
  planned 45-trial study.

Both sets of limits are recorded in `approved_modes`. The 45-trial diagnostic remains
"NOT APPROVED". Freezing refuses to run
outside the launcher environment (the configuration records applied environment
overrides; an early freeze without it produced a non-matching identity and was discarded
before any use). A second unused freeze was discarded after the bundle validator flagged a
synthetic token literal in the V1R4 validator test; the test now builds it at runtime.
Neither discarded manifest was used by any run.

## Files

New: `multi_agent/research_phase1r/{tool_contract,coordination_audit,effect_attribution,
schema_capture,r4_release}.py` (`r4_release.study_summary` reports per-trial and
per-condition counts with explicit unknowns), `scripts/research_phase1/{phase1r4_manual.sh,
prepare_r4_review_bundle.py,validate_r4_review_bundle.py}`,
`tests/test_research_phase1r4.py`, six `docs/research/BACH_*` documents.
Modified: `multi_agent/research_phase1r/{target,release,diagnostic,__main__}.py` (V1R4
branches only; R1–R3 code paths unchanged and their tests pass). Unmodified: vendored
ClawTeam, finance MCP, checker, task, Bank, fixtures, configs, all historical results.
