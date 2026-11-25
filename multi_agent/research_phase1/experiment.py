"""Fixed-condition trials with durable reservations and isolated HarnessAudit state."""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import importlib.metadata
import json
import math
import os
import random
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse

from multi_agent.attacks.gateway import validate_protected_fields
from multi_agent.attacks.models import TaskContract
from multi_agent.cli import RunResultBundle, build_run_context, run_once, write_artifacts
from multi_agent.loader import load_task_with_tools
from multi_agent.local_inference.config import REPO_ROOT, ROLES, load_config
from multi_agent.local_inference.router import check_run_policy
from multi_agent.research_phase1.security import catalog, digest, evaluate

TASKS = {"fin-t1": REPO_ROOT / "multi_agent/tasks/finance/financial_planning/fin-t1.yaml"}
NOTES = Path(__file__).with_name("notes.json")


def read_json(path):
    value = json.loads(Path(path).read_text())
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(path)


def approved_task(value):
    if value in TASKS:
        return TASKS[value]
    path = Path(value).resolve()
    if path in {p.resolve() for p in TASKS.values()}:
        return path
    raise ValueError(f"Task not allowlisted; approved tasks: {list(TASKS)}")


def notes():
    data = read_json(NOTES)
    for condition in ("B0", "B1", "B2"):
        if not data.get(condition):
            raise ValueError(f"Missing fixtures for {condition}")
        for note in data[condition]:
            if set(note) != {"id", "type", "text", "review"}:
                raise ValueError("Malformed requester-note fixture")
    return data


def validate_condition(original, active, note_text):
    validate_protected_fields(original, active)
    contract = TaskContract(scenario_id=original.task_id, task=original)
    if active.goal != contract.compose_goal(note_text).goal:
        raise ValueError("Original goal or requester note differs from approved condition")


def local_config(model, endpoint, tp="auto"):
    if urlparse(endpoint).username is not None or urlparse(endpoint).password is not None:
        raise ValueError("Credentials in MODEL_ENDPOINT are not permitted")
    # Reject contradictory user config before making a single shared-model route.
    cfg = load_config()
    if cfg.inference.mode != "local_only" or cfg.inference.allow_paid_fallback:
        raise ValueError("Phase 1 requires local_only with paid fallback disabled")
    check_run_policy(
        framework="clawteam",
        harness="qwen_code",
        model=None,
        skip_judge=False,
        judge_model=None,
        config=cfg,
    )
    cfg = load_config(
        overrides={
            "inference": {
                "mode": "local_only",
                "model": model,
                "served_model_name": model,
                "endpoint": endpoint,
                "allow_paid_fallback": False,
                "allow_model_download": False,
                "tensor_parallel_size": tp,
            },
            "routes": {r: {"model": model, "endpoint": endpoint} for r in ROLES},
            "parallel": {"max_parallel_trials": 1},
        }
    )
    check_run_policy(
        framework="clawteam",
        harness="qwen_code",
        model=model,
        skip_judge=False,
        judge_model=model,
        config=cfg,
    )
    return cfg


def git_state():
    def git(*args):
        r = subprocess.run(
            ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, timeout=10
        )
        return r.stdout.strip() if r.returncode == 0 else None

    return {
        "commit": git("rev-parse", "HEAD"),
        "dirty_worktree": bool(git("status", "--porcelain")),
        "diff_sha256": digest(git("diff", "HEAD") or ""),
    }


def versions():
    result = {}
    for name in ("HarnessAudit", "clawteam", "pydantic", "mcp"):
        try:
            result[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            result[name] = None
    return result


def implementation_sources():
    paths = list(Path(__file__).parent.glob("*.py")) + [NOTES]
    paths += list((REPO_ROOT / "scripts/research_phase1").glob("*.sh"))
    paths += [
        REPO_ROOT / p
        for p in (
            "multi_agent/cli.py",
            "multi_agent/runner.py",
            "multi_agent/loader.py",
            "multi_agent/checker.py",
            "multi_agent/frameworks/clawteam/prompts.py",
            "multi_agent/frameworks/native/qwen_code/runtime.py",
            "multi_agent/frameworks/native/qwen_code/ingestor.py",
            "multi_agent/completion_judge.py",
            "multi_agent/operational_judge.py",
            "multi_agent/tools/finance.yaml",
            "multi_agent/banks/finance.py",
            "multi_agent/banks/__init__.py",
            "multi_agent/schemas/task.py",
            "multi_agent/schemas/access_rules.py",
        )
    ]
    paths += list((REPO_ROOT / "multi_agent/fixtures/finance").glob("*.json"))
    return {str(p.relative_to(REPO_ROOT)): p for p in paths}


@contextlib.contextmanager
def experiment_lock(root):
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".experiment.lock").open("a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("Another process is using this experiment directory") from exc
        yield


def reserve(root, configuration, max_runs):
    path = root / "run_manifest.json"
    if path.exists():
        manifest = read_json(path)
        if manifest["configuration"] != configuration or manifest["max_target_runs"] != max_runs:
            raise ValueError("Resume configuration/budget differs; use a new output directory")
    else:
        manifest = {
            "experiment_id": uuid.uuid4().hex,
            "configuration": configuration,
            "max_target_runs": max_runs,
            "attempts": [],
            "git": git_state(),
            "versions": versions(),
        }
    # A reserved, interrupted or failed attempt consumes budget forever.
    save(path, manifest)
    return manifest


@contextlib.contextmanager
def observe_judges():
    """Observe swallowed judge errors without changing original evaluator behavior."""
    from multi_agent import completion_judge, operational_judge

    observations = {}
    with contextlib.ExitStack() as stack:
        for module, role in (
            (completion_judge, "completion_judge"),
            (operational_judge, "operational_judge"),
        ):
            original = module.create_json_response_async
            status = observations.setdefault(role, {"calls": 0, "valid_scores": 0, "errors": []})

            async def observed(*args, _original=original, _status=status, **kwargs):
                _status["calls"] += 1
                try:
                    result = await _original(*args, **kwargs)
                except Exception as exc:
                    _status["errors"].append(type(exc).__name__)
                    raise
                score = result.get("score") if isinstance(result, dict) else None
                try:
                    valid = (
                        not isinstance(score, bool)
                        and score is not None
                        and math.isfinite(float(score))
                        and 0 <= float(score) <= 1
                    )
                except (TypeError, ValueError):
                    valid = False
                if valid:
                    _status["valid_scores"] += 1
                else:
                    _status["errors"].append("missing_or_invalid_score")
                return result

            stack.enter_context(patch.object(module, "create_json_response_async", observed))
        yield observations


def execute_target(
    task_path, active, folder, config, *, skip_judge, mock=False, agent_timeout=300, max_turns=30
):
    args = argparse.Namespace(
        task=str(task_path),
        model=config.inference.model,
        framework="clawteam",
        harness="qwen_code",
        agent_timeout=agent_timeout,
        skip_judge=skip_judge,
        judge_model=config.inference.model,
        judge_workers=1,
        output=str(folder / "results"),
        trace_dir=str(folder / "traces"),
        max_turns=max_turns,
        perturbation_id=None,
        perturbation_index=None,
        allow_stale_perturbation=False,
    )
    save(folder / "inference_config.json", config.model_dump(mode="json"))
    env = {
        "HA_CONFIG": str(folder / "inference_config.json"),
        "HA_SET": "",
        "HA_CLUSTER_PROFILE": "",
        "HA_INFERENCE_MODE": "local_only",
        "MASP_NATIVE_ARTIFACT_DIR": str(folder / "native"),
        "MASP_WORKSPACES_ROOT": str(folder / "workspaces"),
    }
    if mock:
        env["HA_SKIP_ENDPOINT_PREFLIGHT"] = "1"
    # Remove ambient route overrides: the recorded configuration controls all trials.
    for key in os.environ:
        if key.startswith("HA_ROUTE_"):
            env[key] = ""
    clean_env = {k: v for k, v in os.environ.items() if not k.startswith(("HA_", "MASP_"))}
    for key in ("HA_SERVER_STATE_DIR", "HA_VLLM_BIN", "HA_VLLM_VENV"):
        if key in os.environ:
            clean_env[key] = os.environ[key]
    clean_env.update(env)
    with patch.dict(os.environ, clean_env, clear=True), contextlib.ExitStack() as stack:
        if mock:
            from .mock import MockAdapter

            stack.enter_context(
                patch("multi_agent.cli._inference_provenance", return_value={"mock": True})
            )
            stack.enter_context(
                patch("multi_agent.runner.get_framework_adapter", return_value=MockAdapter())
            )
        ctx = build_run_context(
            args,
            task_path=task_path,
            pkg_root=REPO_ROOT / "multi_agent",
            repo_root=REPO_ROOT,
            harness="qwen_code",
        )
        ctx.task = active
        ctx.runner.task = active
        result = RunResultBundle(trace_path=ctx.trace_path)
        error = None
        with observe_judges() as judge_observations:
            try:
                run_once(ctx, result)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            finally:
                save(folder / "judge_observations.json", judge_observations)
                write_artifacts(ctx, result)
        trace = result.trace
        if trace:
            save(folder / "execution_trace.json", trace.model_dump(mode="json"))
        result_path = (
            ctx.output_dir / f"{ctx.artifact_prefix}_{trace.run_id}.json"
            if trace and ctx.output_dir
            else None
        )
        return trace, result_path, result.trace_path or ctx.trace_path, error


def run_trials(
    *,
    task="fin-t1",
    conditions=("B0",),
    repeats=1,
    gpu_ids=None,
    model="Qwen/Qwen3-8B",
    endpoint="http://127.0.0.1:8000/v1",
    output_dir,
    seed=0,
    skip_judge=True,
    max_target_runs=1,
    mock=False,
    run_real=False,
    tp="auto",
    agent_timeout=300,
    max_turns=30,
    note_offset=0,
    verification_endpoint=None,
):
    if repeats < 1 or max_target_runs < 1 or repeats * len(conditions) > max_target_runs:
        raise ValueError("Requested trials exceed positive max-target-runs budget")
    if len(set(conditions)) != len(conditions) or not set(conditions) <= {"B0", "B1", "B2"}:
        raise ValueError("Conditions must be a unique subset of B0,B1,B2")
    if not mock and not run_real:
        raise ValueError("Real execution requires RUN_REAL=YES / --run-real and explicit GPU IDs")
    if mock and not skip_judge:
        raise ValueError("Mock integration requires skip-judge; no model requests are made")
    if agent_timeout < 1 or max_turns < 1:
        raise ValueError("Timeout and turn limits must be positive")
    cfg = local_config(model, endpoint, tp)
    path = approved_task(task)
    original, _ = load_task_with_tools(path)
    contract = TaskContract(scenario_id=original.task_id, task=original)
    fixture = notes()
    gpu = None
    if not mock:
        from .gpu import parse_ids, verify_server

        ids = parse_ids(gpu_ids)
        verified_cfg = cfg.model_copy(deep=True)
        if verification_endpoint:
            if urlparse(verification_endpoint).hostname not in {"127.0.0.1", "localhost", "::1"}:
                raise ValueError("The supervised server endpoint must be loopback")
            verified_cfg.inference.endpoint = verification_endpoint
        gpu = verify_server(verified_cfg, ids)
        gpu["routing_endpoint"] = endpoint
    else:
        ids = []
    configuration = {
        "task_id": original.task_id,
        "conditions": list(conditions),
        "repeats": repeats,
        "model": model,
        "endpoint": endpoint,
        "seed": seed,
        "skip_judge": skip_judge,
        "mode": "MOCK" if mock else "REAL_LOCAL",
        "gpu_ids": ids,
        "inference": cfg.model_dump(mode="json"),
        "protected_contract_hash": contract.protected_fingerprint(),
        "original_task_hash": digest(original.model_dump(mode="json")),
        "task_yaml_hash": __import__("hashlib").sha256(path.read_bytes()).hexdigest(),
        "notes_hash": digest(fixture),
        "agent_timeout": agent_timeout,
        "max_turns": max_turns,
        "note_offset": note_offset,
        "verification_endpoint": verification_endpoint,
        "implementation_sha256": {
            name: hashlib.sha256(p.read_bytes()).hexdigest()
            for name, p in implementation_sources().items()
        },
    }
    root = Path(output_dir).resolve()
    failures = 0
    with experiment_lock(root):
        manifest = reserve(root, configuration, max_target_runs)
        save(root / "effective_experiment_config.json", configuration)
        save(root / "requester_notes.json", fixture)
        save(root / "rule_catalog.json", {"rules": catalog(original)})
        for name, source in implementation_sources().items():
            destination = root / "source" / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source.read_bytes())
        if gpu:
            save(root / "gpu_verification.json", gpu)
        schedule = [(c, i) for c in conditions for i in range(repeats)]
        random.Random(seed).shuffle(schedule)
        for condition, repetition in schedule:
            slot = f"{condition}:{repetition}"
            if any(a["slot"] == slot for a in manifest["attempts"]):
                # Resume never reruns an existing slot, including failed/interrupted slots.
                continue
            if len(manifest["attempts"]) >= max_target_runs:
                raise ValueError("Persistent target-run budget exhausted")
            tid = uuid.uuid4().hex
            folder = root / "trials" / tid
            folder.mkdir(parents=True)
            note = fixture[condition][(repetition + note_offset) % len(fixture[condition])]
            active = contract.compose_goal(note["text"])
            validate_condition(original, active, note["text"])
            save(folder / "effective_task.json", active.model_dump(mode="json"))
            attempt = {
                "trial_id": tid,
                "slot": slot,
                "condition": condition,
                "status": "reserved",
                "trial_path": f"trials/{tid}/trial.json",
            }
            manifest["attempts"].append(attempt)
            save(root / "run_manifest.json", manifest)  # before executing anything
            record = {
                "trial_id": tid,
                "experiment_id": manifest["experiment_id"],
                "task_id": original.task_id,
                "condition": condition,
                "note_id": note["id"],
                "note_text": note["text"],
                "note_type": note["type"],
                "mode": configuration["mode"],
                "target_model": model,
                "model_revision": cfg.inference.model_revision,
                "inference_backend": "mock" if mock else "vllm",
                "harness_type": "clawteam/qwen_code",
                "sampling_configuration": None,
                "selected_gpu_ids": ids,
                "effective_cuda_devices": gpu["effective_cuda_devices"] if gpu else None,
                "actual_server_gpu_ids": gpu["actual_server_gpu_ids"] if gpu else None,
                "original_task_hash": configuration["original_task_hash"],
                "effective_task_hash": digest(active.model_dump(mode="json")),
                "protected_contract_hash": contract.protected_fingerprint(),
                "target_run_id": None,
                "execution_status": "reserved",
                "execution_error": None,
                "trajectory_path": None,
                "result_json_path": None,
                "raw_action_count": None,
                "eligible_action_count": None,
                "original_violation_count": None,
                "forbidden_violation_count": None,
                "unnecessary_tool_count": None,
                "violations_by_rule": {},
                "violations_by_type": {},
                "unauthorized_attempt_count": None,
                "unauthorized_effect_count": None,
                "execution_failure": None,
                "completion_failure": None,
                "task_completion": None,
                "completion_evaluation_status": "unavailable",
                "runtime_seconds": None,
                "model_request_count": None,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "seed": seed,
                "seed_scope": "trial ordering only; model seed from server command",
                "git": manifest["git"],
                "task_yaml_hash": configuration["task_yaml_hash"],
                "evaluator_hash": digest(
                    {
                        p: (REPO_ROOT / p).read_text()
                        for p in (
                            "multi_agent/checker.py",
                            "multi_agent/loader.py",
                            "multi_agent/research_phase1/security.py",
                        )
                    }
                ),
            }
            save(folder / "trial.json", record)
            started = time.monotonic()
            trace = None
            obs = evaluate(active, [], tid, complete=False)[1]
            try:
                with (
                    (folder / "execution.log").open("w") as log,
                    contextlib.redirect_stdout(log),
                    contextlib.redirect_stderr(log),
                ):
                    trace, result_path, trace_path, error = execute_target(
                        path,
                        active,
                        folder,
                        cfg,
                        skip_judge=skip_judge,
                        mock=mock,
                        agent_timeout=agent_timeout,
                        max_turns=max_turns,
                    )
                record.update(
                    target_run_id=trace.run_id if trace else None,
                    trajectory_path=str(trace_path),
                    result_json_path=str(result_path)
                    if result_path and result_path.exists()
                    else None,
                )
                if result_path and result_path.exists():
                    record["original_harnessaudit_metrics"] = read_json(result_path)
                if trace is None or error or trace.error:
                    raise RuntimeError(error or (trace.error if trace else "Missing target trace"))
                if not result_path or not result_path.exists():
                    raise ValueError("Missing evaluator result JSON")
                result = read_json(result_path)
                record["original_harnessaudit_metrics"] = result
                research, obs = evaluate(active, trace.actions, tid)
                record.update(research)
                record["research_security_metrics"] = research
                if research["evidence_status"] != "complete_observed_trace":
                    raise ValueError(
                        "Incomplete trace evidence: " + str(research["instrumentation_issues"])
                    )
                if not mock:
                    native = (result.get("inference_provenance") or {}).get("native_summary") or {}
                    agents = native.get("agents") or {}
                    expected = {a.role for a in active.agents}
                    if set(agents) != expected:
                        raise ValueError("Missing per-agent native instrumentation")
                    if any(a.get("result_subtype") != "success" for a in agents.values()):
                        raise ValueError("Native agent failed or completion marker is unavailable")
                    if any(m != model for a in agents.values() for m in a.get("models", [])):
                        raise ValueError("Native agent model differs from experiment contract")
                    if trace.n_tool_calls == 0:
                        raise ValueError(
                            "No finance tool calls; text-only run cannot verify workflow"
                        )
                    if not list((folder / "native").rglob("trace.jsonl")):
                        raise ValueError("Missing native Qwen transcripts")
                record.update(
                    execution_status="success",
                    execution_failure=False,
                    task_completion=trace.task_completion_score if not skip_judge else None,
                    deterministic_completion_score=trace.deterministic_completion_score,
                    completion_evaluation_status="partial_judge_skipped"
                    if skip_judge
                    else (
                        "complete"
                        if trace.llm_completion_score is not None
                        else "partial_judge_unavailable"
                    ),
                )
                if not skip_judge and trace.llm_completion_score is None:
                    record["task_completion"] = None
                judge_observations = read_json(folder / "judge_observations.json")
                record["judge_observations"] = judge_observations
                completion = judge_observations["completion_judge"]
                record["evaluation_failure"] = any(
                    v["errors"] for v in judge_observations.values()
                )
                if not skip_judge and (completion["valid_scores"] == 0 or completion["errors"]):
                    record["task_completion"] = None
                    record["completion_evaluation_status"] = "partial_judge_unavailable"
                record["completion_failure"] = (
                    record["task_completion"] < 1.0
                    if record["task_completion"] is not None
                    else None
                )
                record["research_security_metrics"].update(
                    execution_failure=False,
                    completion_failure=record["completion_failure"],
                    evaluation_failure=record["evaluation_failure"],
                )
                prov = result.get("inference_provenance") or {}
                record["sampling_configuration"] = prov.get("agent_sampling")
                record["model_revision"] = cfg.inference.model_revision
                if gpu:
                    command = gpu["server_record"].get("command") or []
                    if "--revision" in command:
                        record["model_revision"] = command[command.index("--revision") + 1]
                    record["sampling_configuration"] = {
                        "source": "verified server command",
                        "model_seed": command[command.index("--seed") + 1]
                        if "--seed" in command
                        else None,
                        "generation_config": command[
                            command.index("--override-generation-config") + 1
                        ]
                        if "--override-generation-config" in command
                        else None,
                        "note": (
                            "Absent defaults are unavailable; trial seed controls ordering only"
                        ),
                    }
                agents = (prov.get("native_summary") or {}).get("agents") or {}
                request_counts = [v.get("model_requests") for v in agents.values()]
                if request_counts and all(
                    isinstance(v, dict) and v and all(isinstance(n, int) for n in v.values())
                    for v in request_counts
                ):
                    record["target_model_request_count"] = sum(
                        sum(v.values()) for v in request_counts
                    )
                    if skip_judge:
                        record["model_request_count"] = record["target_model_request_count"]
                record["qwen_harness_version"] = prov.get("qwen_code_version")
                record["inference_server_version"] = (
                    prov.get("server") or (gpu["server_record"] if gpu else {})
                ).get("vllm_version")
                record["native_trace_path"] = str(folder / "native")
            except Exception as exc:
                failures += 1
                record.update(
                    execution_status="failed",
                    execution_failure=True,
                    execution_error=f"{type(exc).__name__}: {exc}",
                )
                if trace:
                    research, obs = evaluate(active, trace.actions, tid, complete=False)
                    record.update(research)
                    record["research_security_metrics"] = research
                record.setdefault("research_security_metrics", {}).update(
                    execution_failure=True, completion_failure=None
                )
                with (folder / "execution.log").open("a") as log:
                    log.write(record["execution_error"] + "\n")
            finally:
                record["runtime_seconds"] = time.monotonic() - started
                save(folder / "rule_observations.json", {"observations": obs})
                save(folder / "trial.json", record)
                files = {
                    str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in folder.rglob("*")
                    if p.is_file() and not p.is_symlink() and p.name != "artifact_manifest.json"
                }
                save(folder / "artifact_manifest.json", {"sha256": files})
                attempt["artifact_manifest_sha256"] = hashlib.sha256(
                    (folder / "artifact_manifest.json").read_bytes()
                ).hexdigest()
                attempt["status"] = record["execution_status"]
                save(root / "run_manifest.json", manifest)
        from .reporting import analyze

        report = analyze(root)
        failures = report["failed_or_interrupted_executions"]
    return 1 if failures else 0
