"""``python -m multi_agent.local_inference`` — cost guard, GPUs, local servers.

Commands
--------
audit         full cost audit (routes, harness, credentials, call-path inventory)
verify-cost   exit non-zero unless every route is local/approved (and reachable)
gpu-status    GPUs on this node and which ones this process may use
plan          dry-run placement: which GPUs / TP / replicas a server would use
serve         start (or reuse) a local vLLM server with adaptive GPU placement
status        servers started by this user on this node
stop          stop a server we started (never touches other processes)
health        endpoint health: models, chat, structured output, reasoning, ...
test-tools    real structured tool-calling test (two turns, schema-validated)
benchmark     serving latency/throughput at several concurrency levels
config        print the effective configuration (or one dotted key)
provenance    reproducibility metadata for the current configuration
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from .config import REPO_ROOT, effective_snapshot, get_dotted, load_config


def _cfg(args: argparse.Namespace, extra: dict[str, Any] | None = None):
    overrides = list(getattr(args, "set", None) or [])
    if extra:
        for key, value in extra.items():
            if value is not None:
                text = value if isinstance(value, str) else json.dumps(value)
                overrides.append(f"{key}={text}")
    cfg = load_config(getattr(args, "config", None), profile=getattr(args, "profile", None),
                      overrides=overrides)
    import os

    # Make the resolved config visible to code paths that call current_config().
    if getattr(args, "config", None):
        os.environ["HA_CONFIG"] = str(args.config)
    if getattr(args, "profile", None):
        os.environ["HA_CLUSTER_PROFILE"] = str(args.profile)
    if overrides:
        os.environ["HA_SET"] = ",".join(filter(None, [os.environ.get("HA_SET"), *overrides]))
    return cfg


def _print(data: Any, as_json: bool, text: str | None = None) -> None:
    if as_json:
        print(json.dumps(data, indent=2, default=str))
    else:
        print(text if text is not None else data)


# ------------------------------------------------------------------ commands


def cmd_audit(args: argparse.Namespace, *, verify: bool = False) -> int:
    from .cost_audit import EXIT_CODES, build_report, format_report

    cfg = _cfg(args)
    report = build_report(cfg, probe=not args.static, attacker_backend=args.attacker_backend,
                          harness=args.harness)
    _print(report, args.json, format_report(report, show_paths=not verify))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(report, indent=2))
    if verify:
        code = EXIT_CODES.get(report["verdict"], 2)
        if not args.json:
            print(f"verify-cost: {'PASS' if code == 0 else 'FAIL'} ({report['verdict']}, "
                  f"exit {code})")
        return code
    return 0


def cmd_gpu_status(args: argparse.Namespace) -> int:
    from .gpu_discovery import discover, format_status, permitted_devices

    cfg = _cfg(args)
    snap = discover()
    permitted, why = permitted_devices(snap, selection=cfg.gpu.selection)
    if args.json:
        data = snap.to_dict()
        data["permitted"] = [p.to_dict() for p in permitted]
        data["permitted_reason"] = why
        _print(data, True)
    else:
        print(format_status(snap, permitted, why))
    return 0 if snap.devices else 1


def _gpu_overrides(args: argparse.Namespace) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    if getattr(args, "model", None):
        extra["inference.model"] = args.model
    if getattr(args, "gpu_ids", None):
        extra["gpu.selection"] = "explicit"
        extra["gpu.explicit_ids"] = [int(x) for x in str(args.gpu_ids).split(",") if x.strip()]
    elif getattr(args, "gpu", None):
        extra["gpu.selection"] = args.gpu
    for attr, key in (("port", "inference.port"), ("tp", "inference.tensor_parallel_size"),
                      ("replicas", "inference.data_parallel_replicas"),
                      ("max_model_len", "inference.context_length"),
                      ("gpu_memory_utilization", "inference.gpu_memory_utilization"),
                      ("max_num_seqs", "inference.max_num_seqs"),
                      ("max_num_batched_tokens", "inference.max_num_batched_tokens"),
                      ("safety_margin_gb", "gpu.memory_safety_margin_gb"),
                      ("max_gpu_utilization", "gpu.max_gpu_utilization"),
                      ("host", "inference.host")):
        value = getattr(args, attr, None)
        if value is not None:
            if attr in {"tp", "replicas"} and str(value) != "auto":
                value = int(value)
            extra[key] = value
    if getattr(args, "allow_shared_gpu", False):
        extra["gpu.allow_shared_gpu"] = True
    if getattr(args, "endpoint_port_sync", True) and getattr(args, "port", None):
        host = getattr(args, "host", None) or "127.0.0.1"
        extra["inference.endpoint"] = (
            f"http://{'127.0.0.1' if host in {'0.0.0.0', '::'} else host}:{args.port}/v1")
    return extra


def cmd_plan(args: argparse.Namespace) -> int:
    from .gpu_allocator import GpuReservations, PlacementPolicy, format_plan, plan_placement
    from .gpu_discovery import discover
    from .model_registry import model_spec

    cfg = _cfg(args, _gpu_overrides(args))
    spec = model_spec(cfg.inference.model, cfg.inference.model_revision)
    if spec is None:
        print(f"unknown model {cfg.inference.model}", file=sys.stderr)
        return 2
    plan = plan_placement(discover(), spec, PlacementPolicy.from_config(cfg),
                          reserved=GpuReservations(cfg.gpu.lock_dir).active())
    _print(plan.to_dict(), args.json, format_plan(plan))
    return 0 if plan.ok else 2


def cmd_serve(args: argparse.Namespace) -> int:
    from .server import launch

    cfg = _cfg(args, _gpu_overrides(args))
    res = launch(cfg, name=args.name, reuse=not args.no_reuse, dry_run=args.dry_run,
                 log_dir=Path(args.log_dir) if args.log_dir else None,
                 max_retries=args.max_retries)
    data = res.to_dict()
    if args.record:
        Path(args.record).parent.mkdir(parents=True, exist_ok=True)
        Path(args.record).write_text(json.dumps(data, indent=2, default=str))
    if args.json:
        _print(data, True)
    else:
        from .gpu_allocator import PlacementPlan, format_plan

        if res.plan and not res.reused:
            plan = PlacementPlan(**{k: v for k, v in res.plan.items()
                                    if k in PlacementPlan.__dataclass_fields__
                                    and k not in {"estimate", "candidates"}})
            plan.candidates = []
            print(format_plan(plan))
        for att in res.attempts:
            print(f"attempt {att.get('attempt', 0)}: {att.get('status', 'dry-run')} "
                  f"{att.get('failure', '')}\n  " + " ".join(att["command"]))
        if res.ok and args.dry_run:
            print("DRY RUN: placement planned and command validated; no server started")
        elif res.ok:
            print(("REUSED existing server " if res.reused else "SERVER READY ")
                  + f"name={res.name} endpoint={res.endpoint} pid={res.pid} "
                  f"startup_s={res.startup_s}")
        else:
            print(f"SERVER FAILED: {res.error}", file=sys.stderr)
    return 0 if res.ok else 3


def cmd_status(args: argparse.Namespace) -> int:
    from .server import list_servers

    servers = list_servers()
    if args.json:
        _print(servers, True)
        return 0
    if not servers:
        print("no servers recorded for this user on this host")
    for s in servers:
        print(f"{s['name']}: pid={s['pid']} alive={s['alive']} healthy={s['healthy']} "
              f"endpoint={s['endpoint']} model={s['served_name']} "
              f"CUDA_VISIBLE_DEVICES={s.get('cuda_visible_devices')} "
              f"TP={s.get('tensor_parallel_size')} replicas={s.get('replicas')} log={s['log']}")
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    from .server import list_servers, stop

    names = [s["name"] for s in list_servers()] if args.all else [args.name]
    if not names or names == [None]:
        print("nothing to stop (pass --name NAME or --all)")
        return 0
    code = 0
    for name in names:
        report = stop(name)
        print(json.dumps(report))
        code = code or (0 if report.get("ok") else 1)
    return code


def _endpoint_model(args: argparse.Namespace) -> tuple[str, str, str]:
    from .router import resolve_route

    cfg = _cfg(args)
    route = resolve_route(args.role, cfg)
    endpoint = args.endpoint or route.endpoint
    model = args.model or route.model
    if cfg.inference.mode == "local_only":
        from .endpoints import endpoint_is_free

        if not endpoint_is_free(endpoint, cfg.inference.approved_endpoints):
            raise SystemExit(f"refusing to probe non-local endpoint {endpoint} in local_only mode")
    return endpoint, model, route.api_key


def cmd_health(args: argparse.Namespace, *, only: list[str] | None = None) -> int:
    from .health import results_dict, run_checks

    endpoint, model, key = _endpoint_model(args)
    names = only or args.checks.split(",")
    results = run_checks(endpoint, key, model, names=["models", *[n for n in names
                                                                   if n != "models"]])
    if args.json:
        _print({"endpoint": endpoint, "model": model, "results": results_dict(results)}, True)
    else:
        print(f"endpoint={endpoint} model={model}")
        for r in results:
            lat = f" ({r.latency_s:.2f}s)" if r.latency_s is not None else ""
            print(f"  {r.status:<7} {r.name:<18}{lat} {r.detail}")
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(results_dict(results), indent=2))
    return 0 if all(r.status in {"PASS", "SKIPPED"} for r in results) and \
        results[0].status == "PASS" else 1


def cmd_benchmark(args: argparse.Namespace) -> int:
    from .performance import export_results, run_benchmark

    endpoint, model, key = _endpoint_model(args)
    endpoints = [e.strip() for e in args.endpoints.split(",")] if args.endpoints else [endpoint]
    levels = [int(x) for x in args.concurrency.split(",")]
    gpu_idx = [int(x) for x in args.gpu_indices.split(",")] if args.gpu_indices else None
    results = run_benchmark(endpoints=endpoints, model=model, api_key=key,
                            concurrency_levels=levels, requests_per_level=args.requests,
                            prompt_tokens=args.prompt_tokens, max_tokens=args.max_tokens,
                            unique_prefix=not args.shared_prefix, gpu_indices=gpu_idx,
                            label=args.label)
    out = Path(args.out or REPO_ROOT / "results" / "local_qwen" / "benchmarks" /
               f"{args.label}_{time.strftime('%Y%m%dT%H%M%S')}")
    meta = {"endpoints": endpoints, "model": model, "label": args.label,
            "prompt_tokens": args.prompt_tokens, "max_tokens": args.max_tokens,
            "unique_prefix": not args.shared_prefix, "gpu_indices": gpu_idx,
            "time": time.strftime("%Y-%m-%dT%H:%M:%S")}
    from .server import list_servers

    meta["servers"] = [{k: s.get(k) for k in ("name", "endpoint", "tensor_parallel_size",
                                               "replicas", "cuda_visible_devices",
                                               "gpu_memory_utilization", "max_num_seqs",
                                               "vllm_version")}
                       for s in list_servers() if s.get("endpoint") in endpoints]
    export_results(results, out, meta=meta)
    print(f"{'conc':>4} {'reqs':>4} {'ok':>3} {'wall_s':>7} {'p50_s':>6} {'p95_s':>6} "
          f"{'ttft50':>6} {'tok/s/req':>9} {'agg_tok/s':>9} {'req/s':>6}  gpu")
    for r in results:
        gpu = " ".join(f"{k}:{v['peak_used_gb']}GB/{v['mean_util_pct']}%"
                       for k, v in r.gpu.items())
        print(f"{r.concurrency:>4} {r.requests:>4} {r.ok:>3} {r.wall_s:>7} "
              f"{r.latency_p50_s or 0:>6} {r.latency_p95_s or 0:>6} {r.ttft_p50_s or 0:>6} "
              f"{r.per_request_output_tok_s or 0:>9} {r.aggregate_output_tok_s or 0:>9} "
              f"{r.requests_per_s or 0:>6}  {gpu}")
        for err in r.errors:
            print(f"     error: {err}")
    print(f"results: {out}/benchmark.json and benchmark.csv")
    return 0 if all(r.ok == r.requests for r in results) else 1


def cmd_config(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    if args.get:
        try:
            value = get_dotted(cfg, args.get)
        except KeyError:
            print(f"unknown key {args.get}", file=sys.stderr)
            return 2
        print(json.dumps(value) if isinstance(value, (dict, list)) else value)
        return 0
    _print(effective_snapshot(cfg), True)
    return 0


def cmd_provenance(args: argparse.Namespace) -> int:
    from .provenance import inference_provenance

    _cfg(args)
    _print(inference_provenance(harness=args.harness, framework="clawteam",
                                task_path=args.task, attacker_backend=args.attacker_backend),
           True)
    return 0


# ------------------------------------------------------------------ parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m multi_agent.local_inference",
                                     description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--config", help="YAML config (default configs/local_qwen.yaml)")
        p.add_argument("--profile", help="cluster profile name or path (configs/cluster/*)")
        p.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                       help="dotted override, e.g. --set inference.port=8001 (repeatable)")
        p.add_argument("--json", action="store_true", help="machine-readable output")

    for name, helptext in (("audit", "full cost audit"),
                           ("verify-cost", "fail unless local-only and resolvable")):
        p = sub.add_parser(name, help=helptext)
        common(p)
        p.add_argument("--static", action="store_true", help="do not probe endpoints")
        p.add_argument("--attacker-backend", help="also check an SI-ATG attacker backend")
        p.add_argument("--harness", help="check this harness instead of the default")
        p.add_argument("--out", help="also write the JSON report here")

    p = sub.add_parser("gpu-status", help="GPU inventory and permitted devices")
    common(p)

    def gpu_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--model")
        p.add_argument("--gpu", choices=["auto", "explicit", "scheduler"])
        p.add_argument("--gpu-ids", help="nvidia-smi indices, e.g. 0,2,3 (implies explicit)")
        p.add_argument("--tp", help="tensor parallel size or 'auto'")
        p.add_argument("--replicas", help="data-parallel replicas (int) or 'auto'")
        p.add_argument("--max-model-len", type=int)
        p.add_argument("--gpu-memory-utilization", type=float)
        p.add_argument("--max-num-seqs", type=int)
        p.add_argument("--max-num-batched-tokens", type=int)
        p.add_argument("--safety-margin-gb", type=float)
        p.add_argument("--max-gpu-utilization", type=float)
        p.add_argument("--allow-shared-gpu", action="store_true",
                       help="permit GPUs already used by others (site policy permitting)")
        p.add_argument("--port", type=int)
        p.add_argument("--host", help="bind address (default 127.0.0.1)")

    p = sub.add_parser("plan", help="dry-run GPU placement")
    common(p)
    gpu_args(p)

    p = sub.add_parser("serve", help="start or reuse a local vLLM server")
    common(p)
    gpu_args(p)
    p.add_argument("--name", help="server name (default qwen-<port>)")
    p.add_argument("--no-reuse", action="store_true", help="do not reuse a healthy server")
    p.add_argument("--dry-run", action="store_true", help="plan and print the command only")
    p.add_argument("--log-dir")
    p.add_argument("--record", help="write the launch result JSON here")
    p.add_argument("--max-retries", type=int, default=2, help="OOM retries with smaller config")

    p = sub.add_parser("status", help="servers started by this user on this host")
    common(p)
    p = sub.add_parser("stop", help="stop a server we started")
    common(p)
    p.add_argument("--name")
    p.add_argument("--all", action="store_true", help="stop all of *our* recorded servers")

    for name, helptext in (("health", "endpoint health checks"),
                           ("test-tools", "structured tool-calling test")):
        p = sub.add_parser(name, help=helptext)
        common(p)
        p.add_argument("--endpoint")
        p.add_argument("--model")
        p.add_argument("--role", default="target_specialists")
        p.add_argument("--out")
        if name == "health":
            p.add_argument("--checks", default="chat,structured,reasoning,concurrency,timeout",
                           help="comma list: chat,structured,reasoning,tools,concurrency,timeout")

    p = sub.add_parser("benchmark", help="serving latency/throughput benchmark")
    common(p)
    p.add_argument("--endpoint")
    p.add_argument("--endpoints", help="comma list (client round-robin across servers)")
    p.add_argument("--model")
    p.add_argument("--role", default="target_specialists")
    p.add_argument("--concurrency", default="1,4,8")
    p.add_argument("--requests", type=int, help="requests per level (default 2x concurrency)")
    p.add_argument("--prompt-tokens", type=int, default=1500)
    p.add_argument("--max-tokens", type=int, default=256)
    p.add_argument("--shared-prefix", action="store_true", help="allow prefix-cache hits")
    p.add_argument("--gpu-indices", help="nvidia-smi indices to sample")
    p.add_argument("--label", default="benchmark")
    p.add_argument("--out")

    p = sub.add_parser("config", help="print effective configuration")
    common(p)
    p.add_argument("--get", help="dotted key, e.g. inference.endpoint")

    p = sub.add_parser("provenance", help="reproducibility metadata")
    common(p)
    p.add_argument("--harness", default="qwen_code")
    p.add_argument("--task")
    p.add_argument("--attacker-backend")
    return parser


def main(argv: list[str] | None = None) -> int:
    import logging

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    args = build_parser().parse_args(argv)
    handlers = {
        "audit": cmd_audit,
        "verify-cost": lambda a: cmd_audit(a, verify=True),
        "gpu-status": cmd_gpu_status,
        "plan": cmd_plan,
        "serve": cmd_serve,
        "status": cmd_status,
        "stop": cmd_stop,
        "health": cmd_health,
        "test-tools": lambda a: cmd_health(a, only=["tools"]),
        "benchmark": cmd_benchmark,
        "config": cmd_config,
        "provenance": cmd_provenance,
    }
    return int(handlers[args.command](args) or 0)
