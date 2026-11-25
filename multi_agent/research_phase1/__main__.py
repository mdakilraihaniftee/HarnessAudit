"""Manual BACH Phase 1 interface. Real inference always requires explicit opt-in."""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from pathlib import Path

from .experiment import approved_task, local_config, notes, run_trials, save, versions


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("preflight", "cost", "gpu", "run"):
        p = sub.add_parser(command)
        p.add_argument("--model", default=os.environ.get("MODEL_ID", "Qwen/Qwen3-8B"))
        p.add_argument(
            "--model-endpoint",
            default=os.environ.get("MODEL_ENDPOINT", "http://127.0.0.1:8000/v1"),
        )
        p.add_argument(
            "--tensor-parallel-size", default=os.environ.get("TENSOR_PARALLEL_SIZE", "auto")
        )
        p.add_argument("--task", default="fin-t1")
        p.add_argument("--gpu-ids", default=os.environ.get("GPU_IDS"))
        p.add_argument("--output-dir", default="results/research_phase1/preflight")
        if command == "run":
            p.add_argument("--condition", nargs="+", choices=["B0", "B1", "B2"], default=["B0"])
            p.add_argument("--repeats", type=int, default=1)
            p.add_argument("--max-target-runs", type=int, default=1)
            p.add_argument("--seed", type=int, default=0)
            p.add_argument("--skip-judge", action=argparse.BooleanOptionalAction, default=True)
            p.add_argument(
                "--run-real", action="store_true", default=os.environ.get("RUN_REAL") == "YES"
            )
            p.add_argument("--mock", action="store_true")
            p.add_argument("--agent-timeout", type=int, default=300)
            p.add_argument("--max-turns", type=int, default=30)
    for command in ("analyze", "package"):
        p = sub.add_parser(command)
        p.add_argument("--output-dir", required=True)
        if command == "package":
            p.add_argument("--archive")
    args = parser.parse_args(argv)
    try:
        if args.command == "analyze":
            from .reporting import analyze

            report = analyze(args.output_dir)
            print(json.dumps(report, indent=2))
            return 1 if report["artifact_errors"] else 0
        if args.command == "package":
            from .reporting import package

            print(package(args.output_dir, args.archive))
            return 0
        tp = args.tensor_parallel_size
        if tp != "auto":
            tp = int(tp)
        if args.command == "run":
            return run_trials(
                task=args.task,
                conditions=args.condition,
                repeats=args.repeats,
                gpu_ids=args.gpu_ids,
                model=args.model,
                endpoint=args.model_endpoint,
                output_dir=args.output_dir,
                seed=args.seed,
                skip_judge=args.skip_judge,
                max_target_runs=args.max_target_runs,
                mock=args.mock,
                run_real=args.run_real,
                tp=tp,
                agent_timeout=args.agent_timeout,
                max_turns=args.max_turns,
            )
        cfg = local_config(args.model, args.model_endpoint, tp)
        if args.command == "cost":
            from multi_agent.local_inference.cost_audit import build_report

            report = build_report(
                cfg, probe=False, attacker_backend="qwen_code", harness="qwen_code"
            )
            print(json.dumps(report, indent=2))
            return 0 if report["verdict"] in {"LOCAL_ONLY_VERIFIED", "LOCAL_ONLY_STATIC_OK"} else 1
        if args.command == "gpu":
            from .gpu import inspect, parse_ids, verify_server

            ids = parse_ids(args.gpu_ids)
            report = inspect(cfg, ids)
            try:
                report["existing_server"] = verify_server(cfg, ids)
            except ValueError as exc:
                report["existing_server_error"] = str(exc)
            print(json.dumps(report, indent=2))
            return 0 if report["placement_plan"]["ok"] or "existing_server" in report else 1
        from multi_agent.frameworks.native.qwen_code.runtime import resolve_qwen_bin
        from multi_agent.local_inference.model_registry import local_snapshot
        from multi_agent.local_inference.provenance import _qwen_code_version
        from multi_agent.local_inference.server import endpoint_ready, vllm_bin, vllm_version

        path = approved_task(args.task)
        from multi_agent.attacks.models import TaskContract
        from multi_agent.loader import load_task_with_tools

        task, _ = load_task_with_tools(path)
        contract = TaskContract(scenario_id=task.task_id, task=task)
        for group in notes().values():
            for note in group:
                contract.compose_goal(note["text"])
        failures = []
        try:
            qwen_bin = resolve_qwen_bin(cfg.harness.qwen_code.bin)
        except RuntimeError as exc:
            qwen_bin = None
            failures.append(str(exc))
        try:
            server_version = vllm_version(vllm_bin())
        except RuntimeError as exc:
            server_version = None
            failures.append(str(exc))
        cached = local_snapshot(cfg.inference.model, cfg.inference.model_revision)
        if cached is None:
            failures.append(
                "Model weights not cached; downloads require a separate explicit command"
            )
        ready = endpoint_ready(cfg.inference.endpoint, cfg.inference.served_name)
        if not ready:
            failures.append("Model endpoint unavailable; real tool workflow remains unverified")
        out = Path(args.output_dir)
        out.mkdir(parents=True, exist_ok=True)
        report = {
            "python": platform.python_version(),
            "packages": versions(),
            "task": str(path),
            "si_atg": "imports verified; architecture reused",
            "qwen_binary": qwen_bin,
            "qwen_code_version": _qwen_code_version(qwen_bin) if qwen_bin else None,
            "vllm_version": server_version,
            "cached_model_revision": cached.name if cached else None,
            "model_endpoint_ready": ready,
            "inference_mode": cfg.inference.mode,
            "paid_fallback": False,
            "model_downloads": False,
            "failures": failures,
            "full_finance_workflow_verified": False,
            "note": (
                "This preflight makes no inference request; /models readiness does not verify MCP"
            ),
        }
        save(out / "preflight.json", report)
        print(json.dumps(report, indent=2))
        return 1 if failures else 0
    except (ValueError, RuntimeError, OSError, KeyError) as exc:
        print(f"Phase 1: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
