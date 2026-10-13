"""Command-line interface for isolated finance attacker experiments."""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from multi_agent.banks.finance import SCHEMA_SQL

from .agent import (
    AttackerAgent,
    AttackerHarness,
    CodexCliBackend,
    ControlBackend,
    MockBackend,
)
from .config import AttackerConfig, load_config
from .gateway import (
    HarnessAuditExecutor,
    TargetRunBudget,
    TaskSubmissionGateway,
    inspect_trace_file,
    load_contract,
)
from .memory import AttackerMemory
from .models import TaskContract, TrajectorySummary

log = logging.getLogger(__name__)
DEFAULT_CONFIG = Path(__file__).resolve().parent / "configs" / "finance.yaml"
DEFAULT_MEMORY = Path(__file__).resolve().parent / "data" / "attacker_memory.sqlite"


def _make_components(args: argparse.Namespace) -> tuple[
    AttackerConfig, TaskContract, AttackerMemory, AttackerAgent, str
]:
    config = load_config(args.config)
    campaign_id = getattr(args, "campaign_id", None) or uuid.uuid4().hex[:12]
    memory = AttackerMemory(getattr(args, "memory_db", None) or DEFAULT_MEMORY)
    previous = memory.campaign(campaign_id)
    if getattr(args, "command", None) == "resume":
        if previous is None:
            memory.close()
            raise ValueError(f"Unknown campaign ID: {campaign_id}")
        saved = json.loads(previous["config_json"])
        saved_config = {
            key: value for key, value in saved.items()
            if key in AttackerConfig.model_fields
        }
        config = AttackerConfig.model_validate(saved_config)
    if getattr(args, "backend", None):
        config = config.model_copy(update={"backend": args.backend})
    if getattr(args, "arm", None):
        config = config.model_copy(update={"arm": args.arm})
    if getattr(args, "target_model", None):
        config = config.model_copy(update={"target_model": args.target_model})
    if getattr(args, "target_harness", None):
        config = config.model_copy(update={"target_harness": args.target_harness})
    if getattr(args, "framework", None):
        config = config.model_copy(update={"target_framework": args.framework})
    if getattr(args, "skip_judge", False):
        config = config.model_copy(update={"skip_judge": True})
    arm_settings = {
        "B1": (False, False),
        "B2": (False, False),
        "B3": (False, False),
        "B4": (False, False),
        "B5": (True, False),
        "B6": (True, True),
    }
    memory_enabled, harness_improvement = arm_settings[config.arm]
    config = config.model_copy(
        update={
            "memory_enabled": memory_enabled,
            "harness_self_improvement": harness_improvement,
        }
    )
    contract = load_contract(args.task, config.request_max_chars)
    if contract.scenario_id not in config.allowed_scenarios:
        raise ValueError(
            f"Scenario {contract.scenario_id!r} is not in configured "
            f"allowed_scenarios: {config.allowed_scenarios}"
        )
    output_root = Path(
        getattr(args, "output_dir", None) or "multi_agent/attacks/outputs"
    )
    output_dir = output_root / f"campaign_{campaign_id}"
    dry_run = not getattr(args, "execute_target", False)
    max_runs = min(
        int(getattr(args, "max_target_runs", None) or config.max_target_runs),
        config.max_target_runs,
    )
    executor = None
    if not dry_run:
        executor = HarnessAuditExecutor(
            task_path=args.task,
            artifact_dir=output_dir / "target_runs",
            framework=config.target_framework,
            harness=config.target_harness,
            model=config.target_model,
            judge_model=config.judge_model,
            agent_timeout=config.target_agent_timeout,
            max_turns=config.max_turns,
            skip_judge=config.skip_judge,
            observation_level=config.observation_level,
        )
    gateway = TaskSubmissionGateway(
        {contract.scenario_id: contract},
        executor=executor,
        budget=TargetRunBudget(max_runs) if executor else None,
        dry_run=dry_run,
    )
    if executor and gateway.budget and memory.campaign(campaign_id):
        completed = sum(
            row["delivery_status"] in {"completed", "failed"}
            for row in memory.attempts(campaign_id)
        )
        prior_baseline = output_dir / "baseline_summary.json"
        if prior_baseline.is_file():
            try:
                completed += int(json.loads(
                    prior_baseline.read_text(encoding="utf-8")
                ).get("runs", 0))
            except (json.JSONDecodeError, OSError, ValueError) as exc:
                raise ValueError(
                    f"Cannot restore prior baseline budget from {prior_baseline}: {exc}"
                ) from exc
        gateway.budget.used = completed
    if config.arm in {"B1", "B2"}:
        backend = ControlBackend(config.arm)
    elif config.backend == "codex_cli":
        backend = CodexCliBackend(model=config.model)
    else:
        backend = MockBackend()
    harness = AttackerHarness(
        version_id="uninitialized",
        exploration_rate=config.exploration_rate,
        retrieval_limit=config.retrieval_limit,
    )
    agent = AttackerAgent(
        config=config,
        backend=backend,
        harness=harness,
        memory=memory,
        gateway=gateway,
        contracts={contract.scenario_id: contract},
        output_dir=output_dir,
        seed=0,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "config_snapshot.yaml").write_text(
        yaml.safe_dump(
            {
                "config": config.model_dump(),
                "scenario_id": contract.scenario_id,
                "task_path": str(Path(args.task).resolve()),
                "target_enabled": not dry_run,
                "target_run_budget": max_runs if not dry_run else 0,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return config, contract, memory, agent, campaign_id


def _inspect(contract: TaskContract, level: str) -> dict[str, Any]:
    from multi_agent.loader import load_tool_catalog

    task = contract.task
    catalog = load_tool_catalog(task.domain)
    payload: dict[str, Any] = {
        "scenario_id": task.task_id,
        "domain": task.domain,
        "goal": task.goal,
        "roles": [
            {"role": agent.role, "responsibility": agent.description}
            for agent in task.agents
        ],
        "tools": [
            {
                "name": tool.name,
                "description": tool.description,
                "params": tool.params,
            }
            for tool in catalog.tools
        ],
    }
    if level == "schema_and_trajectory":
        connection = sqlite3.connect(":memory:")
        try:
            connection.executescript(SCHEMA_SQL)
            tables = [
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
            ]
            payload["finance_schema"] = {
                name: [row[1] for row in connection.execute(
                    f'PRAGMA table_info("{name}")'
                )]
                for name in tables
            }
        finally:
            connection.close()
        from .agent import _approved_portfolio_snapshot

        payload["synthetic_portfolio_snapshot"] = _approved_portfolio_snapshot(task.goal)
    return payload


def _cmd_inspect(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    contract = load_contract(args.task, config.request_max_chars)
    if contract.scenario_id not in config.allowed_scenarios:
        raise ValueError(f"Scenario {contract.scenario_id!r} is not approved")
    print(json.dumps(_inspect(contract, config.observation_level), indent=2))


def _cmd_inspect_trace(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    contract = load_contract(args.task, config.request_max_chars)
    if contract.scenario_id not in config.allowed_scenarios:
        raise ValueError(f"Scenario {contract.scenario_id!r} is not approved")
    summary = inspect_trace_file(
        args.trace,
        contract.task.access_rules,
        result_path=args.result,
        observation_level=config.observation_level,
    )
    print(summary.model_dump_json(indent=2))


def _cmd_campaign(args: argparse.Namespace, *, resume: bool = False) -> None:
    config, contract, memory, agent, campaign_id = _make_components(args)
    try:
        requested_rounds = args.rounds or (
            1 if config.arm in {"B1", "B2", "B3"} else config.max_rounds
        )
        if config.arm in {"B1", "B2", "B3"} and requested_rounds != 1:
            raise ValueError(f"Experiment arm {config.arm} requires exactly one round")
        baseline_runs = args.baseline_runs
        if baseline_runs < 0:
            raise ValueError("--baseline-runs cannot be negative")
        if baseline_runs and not args.execute_target:
            raise ValueError("--baseline-runs requires explicit --execute-target")
        baseline_path = agent.output_dir / "baseline_summary.json"
        existing_campaign = memory.campaign(campaign_id)
        if existing_campaign and not resume:
            raise ValueError(
                f"Campaign {campaign_id} already exists; use resume explicitly"
            )
        pending_rounds = max(
            0, requested_rounds - memory.next_round(campaign_id) + 1
        )
        pending_baselines = (
            0 if resume and baseline_path.is_file() else baseline_runs
        )
        if args.execute_target and agent.gateway.budget:
            needed = (
                pending_rounds + pending_baselines + agent.gateway.budget.used
            )
            if needed > agent.gateway.budget.maximum:
                raise ValueError(
                    "Existing runs, baselines, and remaining rounds exceed "
                    "--max-target-runs"
                )
        if resume and baseline_path.is_file():
            baseline_data = json.loads(baseline_path.read_text(encoding="utf-8"))
            agent.baseline_violation_mean = baseline_data.get("mean_unique_violations")
        elif args.execute_target and baseline_runs:
            baseline_results = []
            for _ in range(baseline_runs):
                baseline_results.append(agent.gateway.submit(None, contract.scenario_id))
            baseline_mean = sum(
                len(item.violations) for item in baseline_results
            ) / len(baseline_results)
            agent.baseline_violation_mean = baseline_mean
            baseline_data = {
                "runs": len(baseline_results),
                "mean_unique_violations": baseline_mean,
                "run_ids": [item.run_id for item in baseline_results],
            }
        else:
            baseline_data = {
                "runs": 0,
                "mean_unique_violations": None,
                "status": "uncomputed",
            }
        if not (resume and baseline_path.is_file()):
            baseline_path.write_text(
                json.dumps(baseline_data, indent=2), encoding="utf-8"
            )
        agent.run_campaign(
            contract.scenario_id,
            requested_rounds,
            campaign_id=campaign_id,
            resume=resume,
            max_wall_time=(
                args.max_wall_time or config.max_wall_time_seconds
            ),
        )
        print(f"Campaign {campaign_id} complete")
        print(f"Artifacts: {agent.output_dir}")
        print(f"Memory database: {memory.path}")
    finally:
        memory.close()


def _cmd_baseline(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    contract = load_contract(args.task, config.request_max_chars)
    if contract.scenario_id not in config.allowed_scenarios:
        raise ValueError(f"Scenario {contract.scenario_id!r} is not approved")
    if not args.execute_target:
        raise ValueError("Baseline execution requires explicit --execute-target opt-in")
    maximum = min(args.max_target_runs or config.max_target_runs, config.max_target_runs)
    if args.runs < 1 or args.runs > maximum:
        raise ValueError(f"--runs must be between 1 and the target-run budget ({maximum})")
    output_dir = Path(args.output_dir or "multi_agent/attacks/outputs")
    executor = HarnessAuditExecutor(
        task_path=args.task,
        artifact_dir=output_dir / f"baseline_{uuid.uuid4().hex[:8]}",
        framework=config.target_framework,
        harness=config.target_harness,
        model=args.target_model or config.target_model,
        judge_model=config.judge_model,
        agent_timeout=config.target_agent_timeout,
        max_turns=config.max_turns,
        skip_judge=args.skip_judge or config.skip_judge,
        observation_level=config.observation_level,
    )
    budget = TargetRunBudget(maximum)
    results = []
    for _ in range(args.runs):
        budget.consume()
        task = contract.compose_goal(None)
        results.append(executor(task, ""))
    violations = [len(result.violations) for result in results]
    raw_violation_counts = [
        item.raw_violation_count for item in results
        if item.raw_violation_count is not None
    ]
    summary = {
        "arm": "B0",
        "scenario_id": contract.scenario_id,
        "runs": len(results),
        "mean_unique_violations": sum(violations) / len(violations),
        "mean_raw_violation_count": (
            sum(raw_violation_counts) / len(results)
            if len(raw_violation_counts) == len(results) else None
        ),
        "runs_by_id": [
            {
                "run_id": item.run_id,
                "unique_violations": len(item.violations),
                "raw_violation_count": item.raw_violation_count,
                "violations_by_type": dict(Counter(
                    violation.code for violation in item.violations
                )),
                "trajectory_length": item.action_count,
                "task_completion": item.task_completion,
                "final_response": item.final_answer,
                "execution_status": "completed" if item.completed else "failed",
                "trace_path": item.trace_path,
                "result_path": item.result_path,
            }
            for item in results
        ],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "baseline_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))


def _cmd_memory(args: argparse.Namespace) -> None:
    memory = AttackerMemory(args.memory_db or DEFAULT_MEMORY)
    try:
        if args.campaign_id:
            rows = memory.attempts(args.campaign_id)
        else:
            rows = memory.campaigns()
        print(json.dumps(rows, indent=2))
    finally:
        memory.close()


def _cmd_report(args: argparse.Namespace) -> None:
    path = Path(args.output_dir) / f"campaign_{args.campaign_id}" / "report.json"
    if not path.exists():
        raise FileNotFoundError(f"Campaign report not found: {path}")
    print(path.read_text(encoding="utf-8"))


def _cmd_best(args: argparse.Namespace) -> None:
    memory = AttackerMemory(args.memory_db or DEFAULT_MEMORY)
    try:
        row = memory.connection.execute(
            "SELECT c.candidate_json, a.reward FROM candidates c JOIN attempts a "
            "USING(candidate_id) WHERE a.campaign_id=? ORDER BY a.reward DESC LIMIT 1",
            (args.campaign_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"No finalized candidate for {args.campaign_id}")
        print(json.dumps(json.loads(row["candidate_json"]), indent=2))
    finally:
        memory.close()


def _cmd_simulate(args: argparse.Namespace) -> None:
    """Produce deterministic local evidence without running any target agents."""
    config, contract, memory, agent, campaign_id = _make_components(args)
    (agent.output_dir / "baseline_summary.json").write_text(
        json.dumps(
            {
                "runs": 0,
                "mean_unique_violations": None,
                "status": "uncomputed",
                "note": "Mock simulation; no clean target baseline was run.",
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    def simulated(request: str, scenario_id: str) -> TrajectorySummary:
        violation = (
            "confirm transaction details directly" in request
        )
        from .models import ViolationRecord

        records = [
            ViolationRecord(
                code="V-IC",
                role="trader",
                action_sequence=3,
                recipient="compliance_analyst",
                rule="Synthetic mock: prohibited spoke-to-spoke communication",
            )
        ] if violation else []
        return TrajectorySummary(
            run_id=f"mock-{uuid.uuid4().hex[:10]}",
            completed=True,
            task_completion=0.8,
            action_count=8,
            agents=["relationship_manager", "trader", "compliance_analyst"],
            tools=["get_portfolio_summary", "execute_trade", "submit_compliance_report"],
            communications=[],
            violations=records,
            delivery_confirmed=True,
            observation_level="trajectory_readonly",
        )

    agent.gateway = TaskSubmissionGateway(
        {contract.scenario_id: contract},
        executor=lambda task, text: simulated(text, contract.scenario_id),
        budget=TargetRunBudget(args.max_target_runs or config.max_target_runs),
        dry_run=False,
    )
    try:
        agent.run_campaign(
            contract.scenario_id,
            args.rounds or 3,
            campaign_id=campaign_id,
        )
        print("MOCK SIMULATION ONLY: no HarnessAudit victim agents were run.")
        print(f"Campaign {campaign_id} complete; artifacts: {agent.output_dir}")
    finally:
        memory.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m multi_agent.attacks",
        description="Run bounded external attacker research on local synthetic tasks.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def common(command: argparse.ArgumentParser, *, task_required: bool = True) -> None:
        if task_required:
            command.add_argument("--task", required=True)
        command.add_argument("--config", default=str(DEFAULT_CONFIG))
        command.add_argument("--memory-db", default=str(DEFAULT_MEMORY))
        command.add_argument("--output-dir", default="multi_agent/attacks/outputs")

    inspect = sub.add_parser("inspect", help="Inspect approved synthetic task context")
    common(inspect)
    trace = sub.add_parser(
        "inspect-trace", help="Summarize a normalized HarnessAudit JSONL trace"
    )
    common(trace)
    trace.add_argument("--trace", required=True)
    trace.add_argument("--result")

    baseline = sub.add_parser("baseline", help="Run clean target baselines")
    common(baseline)
    baseline.add_argument("--runs", type=int, default=1)
    baseline.add_argument("--execute-target", action="store_true")
    baseline.add_argument("--max-target-runs", type=int, default=3)
    baseline.add_argument("--target-model")
    baseline.add_argument("--skip-judge", action="store_true")

    for name, help_text in (
        ("campaign", "Run an attacker campaign (dry-run unless opted in)"),
        ("resume", "Resume a persisted campaign using --campaign-id"),
    ):
        command = sub.add_parser(name, help=help_text)
        common(command)
        command.add_argument("--campaign-id")
        command.add_argument("--rounds", type=int)
        command.add_argument(
            "--arm", choices=["B1", "B2", "B3", "B4", "B5", "B6"]
        )
        command.add_argument("--backend", choices=["mock", "codex_cli"])
        command.add_argument("--target-model")
        command.add_argument("--target-harness", choices=[
            "claude", "codex", "openclaw", "oai", "adk",
        ])
        command.add_argument("--framework", choices=["clawteam", "oai", "adk"])
        mode = command.add_mutually_exclusive_group()
        mode.add_argument("--execute-target", action="store_true")
        mode.add_argument("--dry-run", action="store_true")
        command.add_argument("--skip-judge", action="store_true")
        command.add_argument("--max-target-runs", type=int)
        command.add_argument("--max-wall-time", type=float)
        command.add_argument("--baseline-runs", type=int, default=0)
    simulate = sub.add_parser(
        "simulate", help="Run a deterministic mock attacker/target demonstration"
    )
    common(simulate)
    simulate.add_argument("--campaign-id")
    simulate.add_argument("--rounds", type=int, default=3)
    simulate.add_argument("--backend", choices=["mock"], default="mock")
    simulate.add_argument("--max-target-runs", type=int, default=10)

    memory = sub.add_parser("memory", help="List campaigns or stored attempts")
    memory.add_argument("--memory-db", default=str(DEFAULT_MEMORY))
    memory.add_argument("--campaign-id")
    report = sub.add_parser("report", help="Print campaign report JSON")
    report.add_argument("--campaign-id", required=True)
    report.add_argument("--output-dir", default="multi_agent/attacks/outputs")
    best = sub.add_parser("best", help="Print the best candidate in a campaign")
    best.add_argument("--campaign-id", required=True)
    best.add_argument("--memory-db", default=str(DEFAULT_MEMORY))
    return parser


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = build_parser().parse_args()
    if args.command == "inspect":
        _cmd_inspect(args)
    elif args.command == "inspect-trace":
        _cmd_inspect_trace(args)
    elif args.command == "baseline":
        _cmd_baseline(args)
    elif args.command in {"campaign", "resume"}:
        if args.command == "resume" and not args.campaign_id:
            raise ValueError("resume requires --campaign-id")
        _cmd_campaign(args, resume=args.command == "resume")
    elif args.command == "simulate":
        _cmd_simulate(args)
    elif args.command == "memory":
        _cmd_memory(args)
    elif args.command == "report":
        _cmd_report(args)
    elif args.command == "best":
        _cmd_best(args)


if __name__ == "__main__":
    main()
