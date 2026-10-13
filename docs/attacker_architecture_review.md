# SI-ATG architecture review

## Assessment and changes from the initial proposal

This prototype treats the attacker as external in the system-boundary sense:
it runs as a separate process/component, has its own ephemeral LLM context and
SQLite memory, and can interact with the target only by submitting a request
to a trusted gateway. It does not join the ClawTeam roster, invoke FinanceBank
tools, modify the target prompts, or author access rules. On a shared research
machine it is not a separate security principal; process isolation, OS account
isolation, and sandboxing are not claimed.

The threat model is gray-box only when the selected observation level is
`schema_and_trajectory`. Even then, the attacker receives role descriptions,
public tool interfaces, SQLite schema, a restricted synthetic portfolio
snapshot, and its own normalized trajectories—not system prompts, access-rule
definitions, or evaluator credentials. `output_only` removes trajectory-level
fields from persistent attacker feedback. The trusted evaluator still uses
the original benchmark rules. The current prototype defaults to the
schema-and-trajectory setting.

The original goal remains immutable, and the submitted text is a separately
tagged user-level requester note. This preserves task identity and the
authorized objective without filtering out workflow-redirection language.
It is not a semantic guarantee: a request can attempt to contradict the
objective, and measuring whether target agents prioritize it is part of the
experiment.

The attacker is motivated as a controlled red-team research agent probing
workflow boundaries, not as a financial operator. Its objective is measurable
policy-boundary violations in a local synthetic benchmark, with no direct
financial capability. The explicit gateway and the default dry-run are
intentional reductions in operational reach.

The concept differs from fixed benchmark perturbations by maintaining
episodic and strategy memory, selecting request strategies using observed
rewards, mutating prior wording, and evaluating versioned changes to its
own generation/selection configuration. A generated request alone is not a
successful attack. The reported reward is derived from the evaluator and is
penalized for task failure and action count.

## Validity limitations

* Ordinary task difficulty can also cause a violation. A causal claim needs
  matched clean, benign-paraphrase, and attack trials with repeated target
  seeds/models. The prototype does not establish causality from one trace.
* The unique violation key deduplicates repeated instances of the same
  role/rule/resource/action class, but the raw trace count should also be
  retained in future study datasets.
* Longer trajectories incur a small penalty, but this does not fully eliminate
  trajectory-length confounding. Report raw violations, unique violations,
  eligible-action violation rates, and task completion separately.
* A stochastic best-of-many result is subject to selection bias. Fresh
  held-out trials are required before claiming improvement.
* The harness optimizer promotes a changed strategy-weight/guidance snapshot
  only if its training-selected strategy has strictly higher average reward
  on the campaign's held-out attempts. Fewer than two held-out observations,
  no matched strategy, or no improvement results in rejection. This is a
  modest empirical policy update, not autonomous source-code rewriting and
  not sufficient evidence of general self-improvement.
* The holdout is an attempt-index split within one campaign, not an independent
  scenario/model split. It is vulnerable to small-sample noise. Use separate
  development and held-out scenarios and matched target budgets in research
  evaluations.
* Estimated propagation depth is association-based and must not be described
  as causal influence. The trace does not carry a reliable counterfactual
  attribution for instructions.

## Simpler rigorous alternative

For an initial paper-quality study, a frozen request generator plus an
empirical strategy bandit may be preferable to a recursive harness optimizer.
Run repeated clean, benign, fixed-attack, one-shot, memory-only, and
memory-plus-harness arms with equal target budgets; evaluate candidate
improvements on a held-out task family. The implemented optimizer is retained
as an experimental arm, but its decisions and evidence are recorded so it can
be ablated or rejected.

## Design decisions

1. Reused the current `BenchmarkRunner`, `ClawTeamAdapter`, and `check_trace`
   path instead of building a second benchmark executor.
2. Appended an untrusted requester note to a deep-copied goal because the
   current adapter accepts one task input and has no separate requester-message
   contract. No system prompt or specialist prompt is rewritten.
3. Restricted the first task contract allowlist to `fin-t1`; other finance
   tasks can be enabled explicitly in the typed config after review.
4. Kept attacker memory in a separate SQLite file and limited database
   snapshot exposure to schema plus the approved synthetic portfolio records.
5. Used deterministic structured features and strategy means rather than
   introducing embeddings, vector storage, or an unjustified RL framework.
6. Used a read-only ephemeral Codex CLI backend for request generation and
   deterministic mock backends for tests; real target trials remain opt-in.

The verified function-level call graph is documented in
[attacker_execution_flow.md](./attacker_execution_flow.md).
