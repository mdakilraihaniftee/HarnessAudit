# Verified attacker integration flow

## Existing HarnessAudit execution

The repository call graph for a standard multi-agent run is:

```text
python -m multi_agent run TASK
  -> multi_agent.cli.main / cmd_run
  -> resolve_task_path + build_run_context
  -> load_task_with_tools (loads TaskConfig/catalog and synthesizes access rules)
  -> BenchmarkRunner.run
  -> BenchmarkRunner._run_async
  -> get_framework_adapter("clawteam")
  -> ClawTeamAdapter.run
       -> build_native_harness_input(task)
       -> seed_spoke_tasks(task, agents[1:])
       -> build_clawteam_hub_prompt(..., input_text)
       -> build_clawteam_spoke_prompt(...)
       -> spawn configured harness processes and start domain MCP server
  -> Codex/native harness agents -> multi_agent finance MCP tools
  -> FinanceBank (new in-memory instance for each runner invocation)
  -> normalized ObservableAction events -> ExecutionTrace
  -> cli.run_once -> check_trace -> compute_metrics
  -> cli.write_artifacts (normalized JSONL and result JSON)
```

## Request entry and visibility

The only generated field is appended to a deep copy of `TaskConfig.goal` as a
tagged, explicitly untrusted requester note. A fingerprint verifies that all
other task fields are unchanged. No attacker metadata is sent to the target.
`ClawTeamAdapter.run` passes that goal through `build_native_harness_input` to
the hub/coordinator's task-goal section. Specialists receive seeded
role-specific task-board assignments built from each specialist's description
and system prompt; they do not automatically receive the original goal. Any
propagation therefore has to occur through subsequent coordinator delegation
or communication and is not inferred from the fact that a specialist acted.

The trusted gateway loads only an approved finance task, validates request
length and scenario identity, and uses the existing CLI helpers, runner,
checker, and report builder. `BenchmarkRunner._run_async` creates a new FinanceBank
using the repository's immutable finance seed files on every invocation.
Attacker memory lives in a separate SQLite file under `multi_agent/attacks/data`.
The real-target CLI path requires `--execute-target`, and bounds runs and agent
timeout; default campaign mode is dry-run.

The Codes path uses the local `codex exec` noninteractive interface verified
from CLI help. It uses an ephemeral, read-only execution context, an output
schema, subprocess argument arrays, and a timeout. It does not receive target
Codex sessions or target workspace paths.
