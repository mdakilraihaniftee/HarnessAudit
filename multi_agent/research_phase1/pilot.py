"""Supervised fixed 5/5/5 local pilot; one four-replica server, isolated worker processes."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import http.client
import json
import os
import random
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from .cohort_bundle import collect, package_review, report
from .experiment import (
    REPO_ROOT,
    approved_task,
    experiment_lock,
    git_state,
    implementation_sources,
    local_config,
    notes,
    read_json,
    run_trials,
    save,
    versions,
)
from .gpu import parse_ids, validate_ids, verify_server
from .request_proxy import RequestBudget, model_gateway
from .security import catalog, digest


def process_start(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def worker(spec_path):
    spec = read_json(spec_path)
    # Each process has its own Python globals, env changes, bank, workspaces and QWEN_HOME.
    return run_trials(**spec)


def load_ledger(root, config):
    path = root / "pilot_manifest.json"
    if path.exists():
        ledger = read_json(path)
        if ledger["configuration"] != config:
            raise ValueError(
                "Pilot configuration/source differs. Use a new OUTPUT_DIR; retain old evidence"
            )
    else:
        schedule = [
            (c, i)
            for c in ("B0", "B1", "B2")
            for i in range(config["repeats"])
            if (c, i) != ("B0", 0)
        ]
        random.Random(config["seed"]).shuffle(schedule)
        ledger = {
            "pilot_id": uuid.uuid4().hex,
            "configuration": config,
            "git": git_state(),
            "versions": versions(),
            "schedule": [["B0", 0], *schedule],
            "attempts": [],
            "errors": [],
            "cleanup": {},
        }
    save(path, ledger)
    return ledger


def clean_worker_env():
    env = {k: v for k, v in os.environ.items() if not k.startswith(("HA_", "MASP_"))}
    # Worker arguments carry these settings explicitly. In particular, do not
    # leak outer concurrency=4 into the legacy sequential shell-script tests.
    for key in (
        "MAX_PARALLEL_TRIALS",
        "REPEATS",
        "MAX_TARGET_RUNS",
        "MODEL_REQUEST_BUDGET",
        "MODEL_REQUESTS_PER_TRIAL",
        "SKIP_JUDGE",
        "GPU_IDS",
        "MODEL_ID",
        "MODEL_ENDPOINT",
        "OUTPUT_DIR",
        "SEED",
        "AGENT_TIMEOUT",
        "MAX_TURNS",
        "SERVER_PORT",
        "PROXY_PORT",
        "RUN_MOCK",
    ):
        env.pop(key, None)
    from multi_agent.local_inference.endpoints import COMMERCIAL_ENV_VARS

    for key in COMMERCIAL_ENV_VARS:
        env[key] = ""
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    env.update(
        HA_INFERENCE_MODE="local_only",
        HA_ALLOW_MODEL_DOWNLOAD="false",
        HF_HUB_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1",
        RUN_REAL="YES",
    )
    for key in ("HA_SERVER_STATE_DIR", "HA_QWEN_BIN", "HA_VLLM_BIN", "HA_VLLM_VENV"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def start_server(root, cfg, ids):
    from multi_agent.local_inference import server
    from multi_agent.local_inference.gpu_discovery import discover

    snapshot = discover()
    if snapshot.source == "mock":
        raise ValueError("Real launch cannot use mocked GPU discovery")
    permission = validate_ids(ids, snapshot)
    save(root / "gpu_preflight.json", {**permission, "snapshot": snapshot.to_dict()})
    if server.endpoint_ready(cfg.inference.endpoint, cfg.inference.served_name):
        verification = verify_server(cfg, ids)
        state = verification["server_record"]
        if (
            set(verification["actual_server_gpu_ids"]) != set(ids)
            or state.get("replicas") != len(ids)
            or state.get("tensor_parallel_size") != 1
        ):
            raise ValueError("Existing endpoint does not implement requested TP=1 replica layout")
        command = state.get("command") or []
        from multi_agent.local_inference.model_registry import local_snapshot

        checkpoint = local_snapshot(cfg.inference.model, cfg.inference.model_revision)
        if len(command) < 3 or checkpoint is None or command[2] != str(checkpoint):
            raise ValueError(
                "Existing server cached checkpoint revision is unverifiable/different"
            )
        for flag, wanted in (
            ("--max-model-len", str(cfg.inference.context_length)),
            ("--max-num-seqs", str(cfg.inference.max_num_seqs)),
        ):
            if flag not in command or command[command.index(flag) + 1] != wanted:
                raise ValueError("Existing server setting differs: " + flag)
        expected_sampling = {
            k: v
            for k, v in (
                ("temperature", cfg.inference.temperature),
                ("top_p", cfg.inference.top_p),
                ("top_k", cfg.inference.top_k),
            )
            if v is not None
        }
        if (
            "--override-generation-config" not in command
            or json.loads(command[command.index("--override-generation-config") + 1])
            != expected_sampling
        ):
            raise ValueError("Existing server sampling defaults differ from requested settings")
        if "--default-chat-template-kwargs" not in command or json.loads(
            command[command.index("--default-chat-template-kwargs") + 1]
        ) != {"enable_thinking": False}:
            raise ValueError("Existing server reasoning mode differs")
        save(root / "server_verification.json", verification)
        return None
    # A busy GPU is not authorized merely because its processes share this UID.
    for i in ids:
        dev = snapshot.by_index(i)
        if not dev.processes_visible or dev.processes or dev.used_gb > 1:
            raise ValueError(
                f"GPU {i} is occupied or process visibility unavailable; no automatic sharing"
            )
    name = "bach-" + root.name + "-" + uuid.uuid4().hex[:8]
    try:
        launched = server.launch(cfg, name=name, reuse=False, max_retries=0, log_dir=root / "logs")
    except BaseException:
        if server._state_path(name).exists():
            server.stop(name, timeout=10, quiet=True)
        else:
            # Handle an interrupt between Popen and state-file creation. This
            # supervisor has not started target workers yet; inspect only its children.
            from multi_agent.frameworks.clawteam.process_tree import _descendant_pids

            for pid in _descendant_pids(os.getpid()):
                if server._pid_is_ours(pid) and "vllm" in server._cmdline(pid):
                    try:
                        if os.getpgid(pid) == pid:
                            os.killpg(pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
        raise
    save(root / "server_launch.json", launched.to_dict())
    if not launched.ok:
        raise ValueError(launched.error or "Local vLLM launch failed")
    owned = {"name": name, "pid": launched.pid, "start": process_start(launched.pid)}
    save(root / "owned_server.json", owned)
    try:
        save(root / "server_verification.json", verify_server(cfg, ids))
    except BaseException:
        server.stop(name, timeout=10, quiet=True)
        raise
    return owned


@contextlib.contextmanager
def gpu_monitor(root, ids, enabled):
    stop = threading.Event()

    def sample():
        from multi_agent.local_inference.gpu_discovery import discover

        with (root / "gpu_metrics.jsonl").open("a") as handle:
            while not stop.is_set():
                snapshot = discover()
                handle.write(
                    json.dumps(
                        {
                            "timestamp": time.time(),
                            "source": snapshot.source,
                            "devices": [d.to_dict() for d in snapshot.devices if d.index in ids],
                        }
                    )
                    + "\n"
                )
                handle.flush()
                stop.wait(2)

    thread = threading.Thread(target=sample, daemon=True) if enabled else None
    if thread:
        thread.start()
    try:
        yield
    finally:
        stop.set()
        if thread:
            thread.join(timeout=5)


def scrape_metrics(root, endpoint, label):
    url = urlsplit(endpoint)
    connection = http.client.HTTPConnection(url.hostname, url.port or 80, timeout=5)
    try:
        connection.request("GET", "/metrics")
        response = connection.getresponse()
        (root / f"vllm_metrics_{label}.prom").write_bytes(response.read())
    except OSError as exc:
        save(root / f"metrics_{label}_error.json", {"error": type(exc).__name__})
    finally:
        connection.close()


def smoke_usable(root, ledger, mock):
    rows, _ = collect(root, ledger)
    row = next((r for r in rows if r["pilot_slot"] == "B0-0"), None)
    if mock:
        return row is not None and row.get("execution_status") == "success"
    if row is None or row.get("mode") != "REAL_LOCAL":
        return False
    original = row.get("original_harnessaudit_metrics") or {}
    agents = ((original.get("inference_provenance") or {}).get("native_summary") or {}).get(
        "agents"
    ) or {}
    if (
        original.get("n_tool_calls", 0) < 1
        or original.get("n_communications", 0) < 1
        or len(agents) != 4
        or any(a.get("result_subtype") != "success" for a in agents.values())
    ):
        return False
    # Verify actual MCP connectivity in all native init records, not a text-only probe.
    traces = list((root / row["bundle_artifact_prefix"]).glob("trials/*/native/*/*/trace.jsonl"))
    if len(traces) != 4:
        return False
    for path in traces:
        records = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        if not any(
            any(
                m.get("name") == "multi_agent_tools" and m.get("status") == "connected"
                for m in e.get("mcp_servers", [])
            )
            for e in records
            if e.get("type") == "system"
        ):
            return False
    return True


def terminate_workers(active):
    from multi_agent.frameworks.clawteam.process_tree import _descendant_pids

    for proc, _, _ in active.values():
        if proc.poll() is not None:
            continue
        descendants = _descendant_pids(proc.pid)
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            # Only descendants observed under this supervised worker, never arbitrary node PIDs.
            for pid in descendants:
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


def run(args):
    if not args.mock_validation and os.environ.get("RUN_REAL") != "YES":
        raise ValueError("Real launch requires RUN_REAL=YES; no implicit mock fallback")
    if os.environ.get("RUN_MOCK") == "YES" and not args.mock_validation:
        raise ValueError(
            "This launcher does not use RUN_MOCK; real results cannot be replaced by mocks"
        )
    if args.repeats < 5 or 3 * args.repeats != args.target_run_budget:
        raise ValueError("Use at least 5 repeats/condition and an exact 3*REPEATS target budget")
    if (
        not 1 <= args.parallel_trials <= 4
        or min(args.model_request_budget, args.per_trial_requests) < 1
    ):
        raise ValueError("Parallel trials must be 1..4 and request budgets positive")
    if args.agent_timeout < 1 or args.max_turns < 1:
        raise ValueError("Timeout and turn limit must be positive")
    ids = parse_ids(args.gpu_ids)
    if args.parallel_trials > len(ids):
        raise ValueError("Parallel target count exceeds requested replica count")
    root = Path(args.output_dir).resolve()
    endpoint = f"http://127.0.0.1:{args.server_port}/v1"
    cfg = local_config(args.model, endpoint, 1)
    from multi_agent.frameworks.native.qwen_code.runtime import resolve_qwen_bin
    from multi_agent.loader import load_task_with_tools
    from multi_agent.local_inference.model_registry import local_snapshot
    from multi_agent.local_inference.provenance import _qwen_code_version
    from multi_agent.local_inference.server import vllm_bin, vllm_version

    cached = local_snapshot(args.model, cfg.inference.model_revision)
    if cached is None and not args.mock_validation:
        raise ValueError("Qwen checkpoint is not cached; downloads are disabled")
    cfg.inference.model_revision = cached.name if cached else None
    cfg.inference.port, cfg.inference.host = args.server_port, "127.0.0.1"
    cfg.inference.tensor_parallel_size, cfg.inference.data_parallel_replicas = 1, len(ids)
    cfg.inference.allow_model_download = False
    cfg.inference.max_num_seqs = 16
    cfg.inference.api_key_env = None
    cfg.inference.extra_server_args = []
    cfg.gpu.selection, cfg.gpu.explicit_ids = "explicit", ids
    cfg.gpu.allow_shared_gpu, cfg.gpu.allow_retry_smaller = False, False
    cfg.parallel.max_parallel_trials = args.parallel_trials
    cfg.harness.qwen_code.bin = (
        resolve_qwen_bin(cfg.harness.qwen_code.bin) if not args.mock_validation else None
    )
    task_path = approved_task("fin-t1")
    task, _ = load_task_with_tools(task_path)
    config = {
        "mode": "MOCK" if args.mock_validation else "REAL_LOCAL",
        "model": args.model,
        "revision": cfg.inference.model_revision,
        "gpu_ids": ids,
        "replicas": len(ids),
        "tp": 1,
        "repeats": args.repeats,
        "target_run_budget": args.target_run_budget,
        "model_request_budget": args.model_request_budget,
        "per_trial_requests": args.per_trial_requests,
        "parallel_trials": args.parallel_trials,
        "skip_judge": args.skip_judge,
        "agent_timeout": args.agent_timeout,
        "max_turns": args.max_turns,
        "server_port": args.server_port,
        "proxy_port": args.proxy_port,
        "seed": args.seed,
        "inference": cfg.model_dump(mode="json"),
        "task_sha256": hashlib.sha256(task_path.read_bytes()).hexdigest(),
        "notes_sha256": digest(notes()),
        "source_sha256": {
            n: hashlib.sha256(p.read_bytes()).hexdigest()
            for n, p in implementation_sources().items()
        },
    }
    owned, active, ledger, invocation_error = None, {}, None, None
    previous_term = signal.getsignal(signal.SIGTERM)

    def interrupted(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    try:
        with contextlib.nullcontext():  # main holds the lock through cleanup and packaging.
            ledger = load_ledger(root, config)
            for previous in ledger["attempts"]:
                if previous.get("status") == "running":
                    if (
                        previous.get("pid_start") is not None
                        and previous.get("pid")
                        and process_start(previous["pid"]) == previous["pid_start"]
                    ):
                        raise ValueError(
                            "A prior worker is still alive; wait for its recorded "
                            "PID to exit before resuming. No duplicate process started"
                        )
                    previous["status"] = "interrupted"
            save(root / "effective_experiment_config.json", config)
            save(root / "server_config.json", cfg.model_dump(mode="json"))
            save(root / "rule_catalog.json", {"rules": catalog(task)})
            save(root / "requester_notes.json", notes())
            for name, source in implementation_sources().items():
                destination = root / "source" / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(source.read_bytes())
            (root / "logs").mkdir(exist_ok=True)
            (root / "tests").mkdir(exist_ok=True)
            save(root / "tests/check_status.json", {"test_results": "not yet run"})
            if not args.mock_validation:
                save(
                    root / "environment_preflight.json",
                    {
                        "packages": versions(),
                        "qwen_code": _qwen_code_version(cfg.harness.qwen_code.bin),
                        "vllm": vllm_version(vllm_bin()),
                        "cached_revision": cached.name,
                        "local_only": True,
                        "model_downloads": False,
                        "paid_fallback": False,
                    },
                )
            # Record and run the small CPU-only implementation checks for this exact source.
            test_cmd = [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "tests/test_research_phase1.py",
                "tests/test_research_launcher.py",
            ]
            with (root / "tests/pytest.log").open("a") as log:
                check = subprocess.run(
                    test_cmd,
                    cwd=REPO_ROOT,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=180,
                    env=clean_worker_env(),
                )
            save(
                root / "tests/check_status.json",
                {"command": test_cmd, "exit_code": check.returncode},
            )
            if check.returncode:
                raise ValueError("CPU implementation validation failed; see tests/pytest.log")
            if len(ledger["attempts"]) == args.target_run_budget:
                previous_status = report(root, ledger)
                complete = (
                    previous_status["valid_research_executions"] == args.target_run_budget
                    if args.mock_validation
                    else previous_status["all_15_real_targets_completed"]
                )
                return 0 if complete else 1
            if not args.mock_validation:
                owned = start_server(root, cfg, ids)
                scrape_metrics(root, endpoint, "before")
            budget = RequestBudget(
                root / "model_requests.json", args.model_request_budget, args.per_trial_requests
            )
            gateway_context = (
                contextlib.nullcontext(endpoint)
                if args.mock_validation
                else model_gateway(endpoint, args.proxy_port, budget)
            )
            with gateway_context as gateway, gpu_monitor(root, ids, not args.mock_validation):
                # Each child pins its own local route; process globals are isolated.
                base_child_env = clean_worker_env()
                base_child_env["HA_CONFIG"] = str(root / "server_config.json")
                remaining = []
                for condition, repetition in ledger["schedule"]:
                    slot = f"{condition}-{repetition}"
                    if any(a["slot"] == slot for a in ledger["attempts"]):
                        continue
                    remaining.append((condition, repetition))
                while remaining or active:
                    gate_ready = smoke_usable(root, ledger, args.mock_validation)
                    if (
                        not active
                        and not gate_ready
                        and any(a["slot"] == "B0-0" for a in ledger["attempts"])
                    ):
                        raise ValueError(
                            "Clean smoke did not establish real native finance MCP connectivity; "
                            "attempt retained; campaign stopped without replacement"
                        )
                    limit = args.parallel_trials if gate_ready else 1
                    while remaining and len(active) < limit:
                        current_sources = {
                            n: hashlib.sha256(p.read_bytes()).hexdigest()
                            for n, p in implementation_sources().items()
                        }
                        if current_sources != config["source_sha256"]:
                            raise ValueError(
                                "Implementation changed during the pilot; stopping "
                                "before another target is reserved"
                            )
                        if args.smoke_only and gate_ready:
                            remaining.clear()
                            break
                        condition, repetition = remaining.pop(0)
                        slot = f"{condition}-{repetition}"
                        if len(ledger["attempts"]) >= args.target_run_budget:
                            raise ValueError("Persistent target budget exhausted")
                        attempt = {
                            "slot": slot,
                            "condition": condition,
                            "repetition": repetition,
                            "reservation_id": uuid.uuid4().hex,
                            "status": "reserved",
                            "reserved_at": time.time(),
                        }
                        ledger["attempts"].append(attempt)
                        save(root / "pilot_manifest.json", ledger)
                        route = gateway.replace("/v1", "/slot/" + slot + "/v1")
                        spec = {
                            "task": "fin-t1",
                            "conditions": [condition],
                            "repeats": 1,
                            "gpu_ids": args.gpu_ids,
                            "model": args.model,
                            "endpoint": route,
                            "output_dir": str(root / "executions" / slot),
                            "seed": args.seed,
                            "skip_judge": args.skip_judge or args.mock_validation,
                            "max_target_runs": 1,
                            "mock": args.mock_validation,
                            "run_real": not args.mock_validation,
                            "tp": 1,
                            "agent_timeout": args.agent_timeout,
                            "max_turns": args.max_turns,
                            "note_offset": repetition,
                            "verification_endpoint": endpoint
                            if not args.mock_validation
                            else None,
                        }
                        path = root / ".control" / (slot + ".json")
                        save(path, spec)
                        log = (root / "logs" / (slot + ".log")).open("a")
                        proc = subprocess.Popen(
                            [
                                sys.executable,
                                "-m",
                                "multi_agent.research_phase1.pilot",
                                "--worker",
                                str(path),
                            ],
                            cwd=REPO_ROOT,
                            env=base_child_env,
                            stdout=log,
                            stderr=subprocess.STDOUT,
                            start_new_session=True,
                        )
                        attempt.update(
                            status="running", pid=proc.pid, pid_start=process_start(proc.pid)
                        )
                        active[slot] = (proc, attempt, log)
                        save(root / "pilot_manifest.json", ledger)
                        print(
                            f"Started {slot}; "
                            f"{len(ledger['attempts'])}/{args.target_run_budget} reserved",
                            flush=True,
                        )
                        if not gate_ready:
                            break
                    for slot, (proc, attempt, log) in list(active.items()):
                        code = proc.poll()
                        if code is not None:
                            log.close()
                            attempt.update(
                                status="worker_finished", exit_code=code, finished_at=time.time()
                            )
                            del active[slot]
                            save(root / "pilot_manifest.json", ledger)
                            current = report(root, ledger)
                            print(
                                f"Finished {slot}: exit={code}; real completed="
                                f"{current['actual_target_executions_completed']}",
                                flush=True,
                            )
                    if active:
                        time.sleep(0.25)
            if not args.mock_validation:
                scrape_metrics(root, endpoint, "after")
    except (Exception, KeyboardInterrupt) as exc:
        invocation_error = type(exc).__name__
        if ledger is not None:
            ledger["errors"].append(
                {"type": type(exc).__name__, "message": str(exc), "at": time.time()}
            )
        else:
            raise
    finally:
        terminate_workers(active)
        for _, attempt, handle in active.values():
            handle.close()
            attempt.update(status="interrupted", finished_at=time.time())
        if owned:
            from multi_agent.local_inference import server

            if process_start(owned["pid"]) == owned["start"]:
                cleanup = server.stop(owned["name"], timeout=10, quiet=True)
            else:
                cleanup = {"ok": False, "reason": "Process identity changed; refused cleanup"}
            ledger["cleanup"] = cleanup
        elif ledger is not None:
            ledger["cleanup"] = {
                "owned_server_stopped": False,
                "reason": "No server started by this invocation",
            }
        signal.signal(signal.SIGTERM, previous_term)
        if ledger is not None:
            save(root / "pilot_manifest.json", ledger)
            status = report(root, ledger)
            archive = package_review(root, ledger)
            print(json.dumps({"archive": str(archive), "status": status}, indent=2), flush=True)
    if ledger is None:
        return 2
    if args.smoke_only:
        return 0 if smoke_usable(root, ledger, args.mock_validation) else 1
    if args.mock_validation:
        return (
            0
            if status["valid_research_executions"] == args.target_run_budget
            and not invocation_error
            else 1
        )
    return 0 if status["all_15_real_targets_completed"] and not invocation_error else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", type=Path)
    parser.add_argument(
        "--output-dir",
        default=os.environ.get("OUTPUT_DIR", "results/research_phase1/pilot_15_real"),
    )
    parser.add_argument("--gpu-ids", default=os.environ.get("GPU_IDS", "0,1,2,3"))
    parser.add_argument("--model", default=os.environ.get("MODEL_ID", "Qwen/Qwen3-8B"))
    parser.add_argument("--repeats", type=int, default=int(os.environ.get("REPEATS", "5")))
    parser.add_argument(
        "--target-run-budget", type=int, default=int(os.environ.get("MAX_TARGET_RUNS", "15"))
    )
    parser.add_argument(
        "--model-request-budget",
        type=int,
        default=int(os.environ.get("MODEL_REQUEST_BUDGET", "6000")),
    )
    parser.add_argument(
        "--per-trial-requests",
        type=int,
        default=int(os.environ.get("MODEL_REQUESTS_PER_TRIAL", "400")),
    )
    parser.add_argument(
        "--parallel-trials", type=int, default=int(os.environ.get("MAX_PARALLEL_TRIALS", "4"))
    )
    parser.add_argument(
        "--server-port", type=int, default=int(os.environ.get("SERVER_PORT", "8000"))
    )
    parser.add_argument(
        "--proxy-port", type=int, default=int(os.environ.get("PROXY_PORT", "18080"))
    )
    parser.add_argument(
        "--agent-timeout", type=int, default=int(os.environ.get("AGENT_TIMEOUT", "300"))
    )
    parser.add_argument("--max-turns", type=int, default=int(os.environ.get("MAX_TURNS", "30")))
    parser.add_argument("--seed", type=int, default=int(os.environ.get("SEED", "0")))
    parser.add_argument(
        "--skip-judge",
        action=argparse.BooleanOptionalAction,
        default=os.environ.get("SKIP_JUDGE", "0") == "1",
    )
    parser.add_argument("--smoke-only", action="store_true")
    parser.add_argument(
        "--mock-validation",
        action="store_true",
        help="Explicit CPU-only test; never real evidence",
    )
    args = parser.parse_args(argv)
    try:
        if args.worker:
            return worker(args.worker)
        with experiment_lock(Path(args.output_dir).resolve()):
            return run(args)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"BACH launcher: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
