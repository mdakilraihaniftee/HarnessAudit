# Qwen Code harness integration (`--harness qwen_code`)

`qwen_code` runs every ClawTeam agent (coordinator and specialists) as a real
[Qwen Code](https://github.com/QwenLM/qwen-code) CLI process against the local
OpenAI-compatible model server. It is the local counterpart of `--harness codex`
and plugs into the same `HarnessRuntime` protocol, so the ClawTeam adapter, the
task board, the mailbox, the finance MCP server, trace normalization and the
checker are unchanged.

Verified versions: Qwen Code **0.27.4** (Node.js 24), vLLM **0.33.0**,
`Qwen/Qwen3-8B` revision `b968826d9c46dd6066d109eabc6255188de91218`.

## Process layout

```
multi_agent run --harness qwen_code
 └─ ClawTeamAdapter
     ├─ finance MCP bank server  (python, streamable-http on 127.0.0.1:<port>)
     ├─ ClawTeam team + task board + mailbox (CLAWTEAM_DATA_DIR)
     └─ per agent: SubprocessBackend → <run>/bin/qwen (wrapper)
           └─ qwen --bare --auth-type openai --openai-base-url <route> --model <route>
                   --mcp-config agents/<role>/mcp.json --output-format stream-json
                   --append-system-prompt "<role instructions + tool-naming note>"
                   --advisor off --max-session-turns 80 --exclude-tools web_search …
                   --yolo -p "<ClawTeam hub/spoke prompt>"
                │   ├─ MCP: multi_agent_tools (httpUrl, finance tools)
                │   └─ MCP: clawteam (stdio clawteam-mcp: task_*, mailbox_*)
                └─ stdout stream-json → jsonl_stamp.cjs → agents/<role>/trace.jsonl
```

ClawTeam recognises the executable by its basename `qwen`, appends `--yolo -p
<prompt>` itself and wraps the process with its on-exit lifecycle hook, exactly
as for the other CLIs.

## Isolation and cost safety per agent

| Setting | Why |
|---|---|
| `QWEN_HOME=<run>/agents/<role>/qwen_home` with a generated `settings.json` (also passed as `QWEN_CODE_SYSTEM_SETTINGS_PATH`) | no user settings, OAuth tokens, memories, skills or extensions leak in; nothing persists between runs |
| `--bare` | no implicit discovery (context files, extensions, skills, managed memory); only explicit CLI inputs — measured 14 tools instead of ~40 and ~5 s start-up instead of ~14 s |
| `--auth-type openai` + `--openai-base-url` + `--model` from the route | the provider is pinned; `qwen-oauth`/DashScope providers are never selected |
| commercial env vars blanked, placeholder `OPENAI_API_KEY` | CLI flags also outrank `.env` files Qwen Code may read |
| `privacy.usageStatisticsEnabled=false`, `QWEN_USAGE_STATISTICS_ENABLED=0`, `general.enableAutoUpdate=false`, `model.catalogRefresh=false`, `telemetry.enabled=false`, `--advisor off` | no background network traffic |
| `memory.enableManagedAutoMemory/AutoDream/AutoSkill=false` | without this one trivial prompt produced 6 model requests (42.9k tokens) and 5 background file-tool calls |
| `web_search`, `web_fetch` excluded | these can call hosted search APIs |
| `netlog.cjs` Node preload (`HA_QWEN_NETLOG=1`, default) | records every TCP remote address and DNS lookup → `netlog.jsonl` |

## Harness adaptations (and why they do not change the benchmark)

1. **MCP tool naming note.** Qwen Code registers MCP tools as
   `mcp__<server>__<tool>`; the ClawTeam/HarnessAudit prompts name them bare
   (`task_list(...)`). Qwen3-8B copied the bare names literally and every call
   failed with `Tool "task_list" not found in registry` (observed in the first
   run, which HarnessAudit correctly flagged as a degenerate empty run). The
   runtime now appends a short harness note to each agent's system prompt that
   documents the prefix, the same way Claude Code and Codex describe their own
   MCP naming in their built-in prompts. Goals, roles, permissions, rules and
   checkpoints are untouched.
2. **Role instructions.** ClawTeam only injects `--append-system-prompt` for
   Claude and pi; the coordinator's role instructions were otherwise lost for
   Codex as well. `qwen_code` passes each agent's `system_prompt`/description via
   `--append-system-prompt` (an additive `register_agent_instructions` hook in the
   ClawTeam adapter; other runtimes ignore it).
3. **Loop protection off.** Qwen Code's identical-call guard told an agent that
   was legitimately re-polling `task_list` to stop, and it ended its session.
   ClawTeam's protocol requires polling; Codex/Claude have no such guard. It is
   disabled (`model.loopProtection: "off"`); sessions remain bounded by
   `--max-session-turns` and the agent timeout.
4. **No keepalive** (same as Codex): when an agent ends its session, it is done.
5. **Sampling is set by the server.** Verified with `--openai-logging`: in
   `--bare` mode Qwen Code ignores `settings.json` `model.generationConfig` and
   sends no temperature/top-p/top-k and no `chat_template_kwargs` (it sends
   `max_tokens` = context length). Agent sampling therefore comes from the vLLM
   launch flags `--override-generation-config` (from `inference.temperature`,
   `top_p`, `top_k`) and `--default-chat-template-kwargs` (`reasoning_mode`).
   Consequence: per-role `temperature`/`reasoning_mode` overrides apply to judges
   and the `local_openai` attacker (which send explicit parameters) but **not** to
   `qwen_code` agents or the `qwen_code` attacker; give those a separate server if
   they need different sampling. Reports record the effective values
   (`inference_provenance.agent_sampling`, read from the server's launch command).
   Runs made on 2026-11-20 before this fix used the model's `generation_config.json`
   defaults (temperature 0.6, top_p 0.95, top_k 20) with thinking off.

## Trace ingestion

`frameworks/native/qwen_code/ingestor.py` parses the stream-json events
(verified shapes, Qwen Code 0.27.4):

| Native event | Normalized action |
|---|---|
| `assistant` → `tool_use{id,name,input}` + `user` → `tool_result{tool_use_id,is_error,content}` for `mcp__multi_agent_tools__*` | `tool_call` (paired by id; tool name normalized with the shared `normalize_mcp_tool_name`) |
| same pair for `mcp__clawteam__mailbox_send` / `mailbox_broadcast`, successful and for this run's team | `communication` (one per recipient; broadcast expanded) |
| hub's `result.result` (else last assistant text) | final `user` communication |
| `tool_use` with no result (truncated session) | `tool_call` with `(no result recorded)` |
| native file/shell tools in SDE MCP-only mode | `__native_bypass__…` tool call, same as other harnesses |

Recorded per action: agent role, tool name/arguments/result, `is_error`,
source file and line, and `timestamp_source: wrapper_receive_time`.

**Missing observability (recorded, not invented):** Qwen Code's stream-json has no
per-event timestamp. The wrapper stamps each line on receipt (`_ha_recv_ts`, ms
resolution); ordering across agents uses that time. Token usage is only available
per session (`result.usage`) and per model in `usage_record.jsonl`, not per
request.

Native artifacts are exported for every run to
`<trace-dir>/qwen_code/<model>/native/<run_id>/<role>/` (`trace.jsonl`,
`netlog.jsonl`, `system_prompt.md`, `mcp.json`, `settings.json`,
`usage_record.jsonl`, `stderr.log`) plus `native_summary.json`; the run report's
`inference_provenance` block links them.

## Pre-existing bug: the runner consumed the coordinator's mailbox

**Affects every ClawTeam harness, not only `qwen_code`.** The adapter waits for
the team with ClawTeam's `TaskWaiter(agent_name=hub_role)`. `TaskWaiter.wait()`
calls `MailboxManager.receive(hub_role)` every 5 s, and `receive` is
*destructive* (claim/consume); no `on_message` handler was set, so specialist
reports addressed to the coordinator were deleted before the coordinator agent
could `mailbox_receive` them. Deterministic reproduction (no model):
`tests/test_local_inference.py::test_waiter_no_longer_steals_hub_mailbox`.

Observed effect with local agents: the specialist's `mailbox_send` succeeded, the
coordinator polled its inbox after the send and always got `[]` (3/3 synthetic
runs); after the fix the report arrived on the first attempt.

Fix: the waiter polls a dedicated observer inbox (`__harnessaudit_waiter__`)
that no agent writes to. To keep previously published hosted-harness results
comparable, it is **on by default only for `qwen_code`**. Legacy harnesses keep
the historical behaviour unless `MASP_PRESERVE_HUB_INBOX=1`;
`MASP_PRESERVE_HUB_INBOX=0` forces the historical behaviour everywhere. Whether
to enable it for hosted runs (and whether earlier hosted results were affected)
is a research decision for the benchmark owners.

## Early wait termination

ClawTeam's `TaskWaiter` waits until all tasks are completed or the timeout
expires. When all agents of a non-keepalive harness have exited, nothing can
progress, so the adapter now stops waiting (`agents_exited`) instead of idling to
the timeout. This affects scheduling only, not what is recorded.
`MASP_WAIT_FULL_TIMEOUT=1` restores the old behaviour.

## Attacker backend

`--backend qwen_code` runs `qwen --bare --output-format json --json-schema
@schema.json --max-tool-calls 0` with the generation prompt on stdin, in a fresh
temporary `QWEN_HOME` and workspace per call (no state carries between calls; the
attacker's memory is the SQLite memory DB, injected through the prompt as
before). `--max-tool-calls 0` allows only the synthetic `structured_output` tool.
The learning loop (`AttackerAgent`: memory retrieval, strategy selection,
reflection, harness optimisation) is unchanged; each round's exact generator
input is now saved under `prompts/round_NNNN.txt` and the memory records it used
under `generation_metadata.memory_attempt_ids`.

`--backend local_openai` does the same with one chat-completions call and
server-side JSON-schema decoding (useful without Node.js).

## Known limitations

* Qwen3-8B is a weak coordinator: in fin-t1 it often ends its session after
  delegating ("I will wait for their reports") instead of polling the mailbox,
  so it does not relay IDs; specialists then guess identifiers and the
  deterministic checkpoints fail. It also tends to fill optional tool arguments
  with invented values (e.g. `msg_type: "report"`, which ClawTeam rejects). These
  are model-capability limitations: the synthetic workflow
  (`tests/fixtures/synthetic/syn-mailbox.yaml`, run by `08_test_clawteam.sh`)
  passes all 11 integration criteria with the same harness.
  Larger local models (Qwen3-14B/32B, Qwen3-30B-A3B) are configurable.
* Qwen Code's own prompt and tool schemas use roughly 5k (bare) to 13k (full)
  tokens of context per request; keep `context_length` at 32k for Qwen3.
* `Node.js 24` triggers a Qwen Code warning about memory use in shell safety
  checks; it is harmless here (shell tools are rarely used by finance agents).
