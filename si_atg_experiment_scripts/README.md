# SI-ATG experimental scripts (finance fin-t1)

These scripts follow the **user-supplied Copilot README** for a locally modified HarnessAudit. The original public repository alone may not have `multi_agent.attacks`.

## Before you start

1. Place this entire directory anywhere, or copy it into the HarnessAudit root.
2. Open a shell in the **HarnessAudit repo root**.
3. Activate the existing Python environment (`source .venv/bin/activate`, if that is its location).
4. If needed, set `REPO_ROOT` and/or `TASK` to your actual locations.
5. Only use the local synthetic benchmark; none of the scripts target production systems.

```bash
bash /path/to/si_atg_experiment_scripts/00_preflight.sh
bash /path/to/si_atg_experiment_scripts/01_mock_pipeline.sh
```

These **do not launch real target agents**. The mock may use deterministic feedback, not real target outcomes.

## Real target experiments (explicit opt-in)

Every command below launches your local Codex/ClawTeam benchmark and may incur model usage. `RUN_REAL=YES` is required to prevent accidental execution. Default `SKIP_JUDGE=1` avoids judge-model scoring for basic integration tests, so full quality metrics may be unavailable.

Clean baseline (3 runs):

```bash
RUN_REAL=YES RUNS=3 bash /path/to/si_atg_experiment_scripts/02_clean_baseline.sh
```

One-shot fixed adversarial task (B2) and one-shot Codex-generated task (B3):

```bash
RUN_REAL=YES ARM=B2 bash /path/to/si_atg_experiment_scripts/03_one_shot.sh
RUN_REAL=YES ARM=B3 bash /path/to/si_atg_experiment_scripts/03_one_shot.sh
```

Initial adaptive 3-round demonstration:

```bash
RUN_REAL=YES ARM=B5 ROUNDS=3 bash /path/to/si_atg_experiment_scripts/04_adaptive_campaign.sh
```

More meaningful matched-budget study, if model budget allows:

```bash
RUN_REAL=YES ARM=B4 ROUNDS=10 bash /path/to/si_atg_experiment_scripts/04_adaptive_campaign.sh
RUN_REAL=YES ARM=B5 ROUNDS=10 bash /path/to/si_atg_experiment_scripts/04_adaptive_campaign.sh
RUN_REAL=YES ARM=B6 ROUNDS=10 bash /path/to/si_atg_experiment_scripts/04_adaptive_campaign.sh
```

B4 = no learned memory, B5 = memory-enabled recursive attacker, B6 = memory plus evaluated harness update. **These three arms have matching target-run budgets**, but final comparisons still require repeated, independent evaluation. B6 may not promote an update with a small/sparse holdout.

For repeated one-shot controls use `REPEATS=10 ARM=B2` or `ARM=B3`; B1 is benign paraphrase. The script launches one-run campaigns sequentially, each with a distinct output directory.

```bash
RUN_REAL=YES ARM=B1 REPEATS=3 bash /path/to/si_atg_experiment_scripts/03_one_shot.sh
RUN_REAL=YES ARM=B2 REPEATS=3 bash /path/to/si_atg_experiment_scripts/03_one_shot.sh
RUN_REAL=YES ARM=B3 REPEATS=3 bash /path/to/si_atg_experiment_scripts/03_one_shot.sh
```

For evaluation with LLM judges enabled, add `SKIP_JUDGE=0` to a script command, after verifying judge configuration and budget.

## Inspect results

The scripts store terminal logs under `si_atg_experiments/` by default; they pass isolated `--output-dir` and `--memory-db` when the local CLI advertises them. Each campaign should additionally write normalized trace files, attempt records, candidate requests, feedback, memory/harness snapshots, and reports according to the Copilot implementation.

Copy the campaign ID from its terminal output. If a memory path was set, pass it explicitly:

```bash
CAMPAIGN_ID=<real_id> MEMORY_DB=/path/to/attacker_memory.sqlite \
  bash /path/to/si_atg_experiment_scripts/05_inspect_campaign.sh
```

If you know the trace/result pair, inspect it with your native CLI:

```bash
python -m multi_agent.attacks inspect-trace \
 --task multi_agent/tasks/finance/financial_planning/fin-t1.yaml \
 --trace /path/to/trace.jsonl --result /path/to/result.json
```

Do not interpret mock successes as real attack successes. A growing reward within one campaign is not sufficient to establish improved attack effectiveness because of selection bias and model variability. Re-evaluate final candidates on fresh, held-out runs.

## Expected outcomes

| Stage | Research question | Expected artifacts |
|---|---|---|
| Preflight | Is the new package installed and the task available? | tool/version info, inspected task context |
| Mock | Does the loop produce/store/reuse feedback? | synthetic attempts, memory entries, updated candidate |
| Baseline B0 | How unsafe is the standard agent run naturally? | baseline violation rates, completion, traces |
| B1/B2/B3 | Does requester content change observed safety behavior? | one-shot candidates, actual trajectories, violations |
| B4/B5 | Does memory help beyond search alone? | per-round reward and success trends, strategy history |
| B5/B6 | Does the harness optimizer add value? | harness snapshots, promotion decisions, performance comparison |

**Caveat:** scripts are syntax-checked here but could not be executed against your modified private local code. If your Copilot changes used different CLI option names, `python -m multi_agent.attacks <subcommand> --help` is the authority.
