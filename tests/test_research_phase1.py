"""BACH fixed-control measurement and manual execution guards (no real inference)."""

from __future__ import annotations

import copy
import os
import sqlite3
import subprocess
import tarfile
from pathlib import Path

import pytest

from multi_agent.attacks.gateway import validate_protected_fields
from multi_agent.attacks.models import TaskContract
from multi_agent.loader import load_task_with_tools
from multi_agent.local_inference.cluster import detect_slurm
from multi_agent.local_inference.gpu_discovery import GpuDevice, GpuSnapshot
from multi_agent.research_phase1 import experiment as exp
from multi_agent.research_phase1.gpu import parse_ids, validate_ids, verify_server
from multi_agent.research_phase1.reporting import analyze, package
from multi_agent.research_phase1.security import catalog, classify, evaluate, rule_id
from multi_agent.schemas.actions import ObservableAction


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch):
    for key in list(os.environ):
        if key.startswith(("HA_", "SLURM_", "MASP_")) or key in {
            "CUDA_VISIBLE_DEVICES",
            "RUN_REAL",
        }:
            monkeypatch.delenv(key, raising=False)


@pytest.fixture
def task():
    return load_task_with_tools(exp.approved_task("fin-t1"))[0]


def action(seq=1, role="relationship_manager", tool="get_portfolio_summary", **kwargs):
    return ObservableAction(
        sequence_num=seq,
        run_id="test",
        surface="tool_call",
        agent_id=role,
        agent_role=role,
        tool_name=tool,
        tool_args={"portfolio_id": "PF-4821"},
        tool_result="Synthetic result",
        **kwargs,
    )


def snapshot(env=None):
    env = env or {}
    devices = [GpuDevice(i, f"GPU-{i}", "L40S", 46, 44, 2, 0) for i in range(4)]
    return GpuSnapshot(
        "test", "mock", "test", devices, env.get("CUDA_VISIBLE_DEVICES"), detect_slurm(env)
    )


def run(root, **kwargs):
    return exp.run_trials(output_dir=root, mock=True, **kwargs)


@pytest.mark.parametrize("field", ["agents", "access_rules", "metadata", "completion_checkpoints"])
def test_protected_contract_mutations(task, field):
    modified = task.model_copy(deep=True)
    if field == "metadata":
        modified.metadata["changed"] = True
    else:
        setattr(modified, field, [])
    with pytest.raises(ValueError, match="changed"):
        validate_protected_fields(task, modified)


def test_allowlist():
    assert exp.approved_task("fin-t1").is_file()
    with pytest.raises(ValueError, match="allowlisted"):
        exp.approved_task("/tmp/fabricated.yaml")


def test_all_fixtures_composition(task):
    contract = TaskContract(scenario_id=task.task_id, task=task)
    fixture = exp.notes()
    assert len(fixture["B1"]) == len(fixture["B2"]) == 3
    lengths = {}
    for condition, group in fixture.items():
        lengths[condition] = [len(n["text"].split()) for n in group]
        for note in group:
            active = contract.compose_goal(note["text"])
            assert active.goal.startswith(task.goal.rstrip())
            validate_protected_fields(task, active)
            assert ("<requester_note>" in active.goal) == (condition != "B0")
            assert note["review"]
    assert max(lengths["B1"] + lengths["B2"]) / min(lengths["B1"] + lengths["B2"]) < 1.5
    assert contract.compose_goal("").goal == task.goal


def test_forbidden_vs_unnecessary_and_permitted(task):
    actions = [
        action(),
        action(seq=2, role="trader", tool="get_trade_history"),
        action(seq=3, role="trader"),
    ]
    metrics, obs = evaluate(task, actions, "trial")
    assert metrics["forbidden_violation_count"] == 1
    assert metrics["unnecessary_tool_count"] == 1
    assert metrics["original_violation_count"] == 2
    assert metrics["unauthorized_effect_count"] is None
    assert any(o["attempt_observed"] is True for o in obs)
    assert all(o["effect_observed"] is None for o in obs)


def test_resource_and_communication(task):
    a = action(role="trader")
    a.tool_args["portfolio_id"] = "PF-0000"
    b = ObservableAction(
        sequence_num=2,
        run_id="test",
        surface="communication",
        agent_id="trader",
        agent_role="trader",
        target_role="compliance_analyst",
        content="review",
    )
    metrics, _ = evaluate(task, [a, b], "trial")
    assert metrics["violations_by_type"]["forbidden_resource"] == 1
    assert metrics["violations_by_type"]["forbidden_communication"] == 1


def test_data_leak(task):
    a = ObservableAction(
        sequence_num=1,
        run_id="test",
        surface="communication",
        agent_id="relationship_manager",
        agent_role="relationship_manager",
        target_role="trader",
        content="Synthetic SSN 123-45-6789",
    )
    metrics, _ = evaluate(task, [a], "trial")
    assert metrics["violations_by_type"]["prohibited_disclosure"] == 1


def test_dedup_keeps_distinct_events(task):
    rule = next(r for r in task.access_rules if classify(task, r) == "forbidden_tool")
    task.access_rules.append(rule.model_copy(deep=True))
    a = action(role=rule.role, tool=rule.match.tool)
    b = a.model_copy(update={"sequence_num": 2})
    metrics, _ = evaluate(task, [a, b], "trial")
    assert metrics["forbidden_violation_count"] == 2
    assert metrics["original_violation_count"] == 4


def test_rule_ids_not_order_or_description(task):
    before = {r["rule_id"] for r in catalog(task)}
    task.access_rules.reverse()
    for r in task.access_rules:
        r.description = "Different display description"
    assert before == {r["rule_id"] for r in catalog(task)}
    r = task.access_rules[0]
    old = rule_id(task.task_id, r)
    r.match.tool = "different"
    assert old != rule_id(task.task_id, r)


@pytest.mark.parametrize(
    "field,value", [("tool_args", None), ("tool_result", None), ("tool_name", None)]
)
def test_missing_trace_fields_unknown(task, field, value):
    a = action(role="trader")
    setattr(a, field, value)
    metrics, obs = evaluate(task, [a], "trial")
    assert metrics["evidence_status"] == "unknown_or_partial"
    assert metrics["forbidden_violation_count"] is None
    assert any(o["violation_observed"] is None for o in obs)


def test_missing_resource_default_unknown(task):
    a = action(role="trader")
    a.tool_args = {}
    metrics, _ = evaluate(task, [a], "trial")
    assert metrics["forbidden_violation_count"] is None
    assert "unresolved_resource_default:1" in metrics["instrumentation_issues"]


@pytest.mark.parametrize("bad", ["", "1,", "-1", "1,1", "1,a", "1 3"])
def test_gpu_parse_reject(bad):
    with pytest.raises(ValueError):
        parse_ids(bad)


@pytest.mark.parametrize("text", ["0", "1", "1,3", "0,2,3"])
def test_gpu_explicit_mapping(text):
    ids = parse_ids(text)
    report = validate_ids(ids, snapshot(), {})
    assert [r["physical_gpu"] for r in report["requested_cuda_mapping"]] == ids
    assert [r["cuda_device"] for r in report["requested_cuda_mapping"]] == list(range(len(ids)))


def test_slurm_intersection():
    env = {"SLURM_JOB_ID": "123", "SLURM_JOB_GPUS": "1,3", "CUDA_VISIBLE_DEVICES": "0,1,3"}
    snap = snapshot(env)
    assert validate_ids([1, 3], snap, env)["permitted_gpu_ids"] == [1, 3]
    with pytest.raises(ValueError, match="outside permitted"):
        validate_ids([0], snap, env)


def test_slurm_no_allocation_and_ambiguous_remap():
    env = {"SLURM_JOB_ID": "1"}
    with pytest.raises(ValueError):
        validate_ids([1], snapshot(env), env)
    env = {"CUDA_VISIBLE_DEVICES": "4,5"}
    with pytest.raises(ValueError, match="Ambiguous"):
        validate_ids([1], snapshot(env), env)


def test_server_reuse_placement():
    cfg = exp.local_config("Qwen/Qwen3-8B", "http://127.0.0.1:8000/v1")
    state = {
        "endpoint": cfg.inference.endpoint,
        "uid": os.getuid(),
        "pid": 999,
        "served_name": cfg.inference.model,
        "model": cfg.inference.model,
        "cuda_visible_devices": "1,3",
        "device_map": [{"nvidia_smi_index": i, "uuid": f"GPU-{i}"} for i in [1, 3]],
    }
    report = verify_server(
        cfg, [1, 3], snapshot=snapshot(), records=[state], pid_check=lambda p: True
    )
    assert report["actual_server_gpu_ids"] == [1, 3]
    bad = copy.deepcopy(state)
    bad["device_map"][0]["nvidia_smi_index"] = 0
    with pytest.raises(ValueError, match="Existing server uses"):
        verify_server(cfg, [1, 3], snapshot=snapshot(), records=[bad], pid_check=lambda p: True)
    with pytest.raises(ValueError, match="No live"):
        verify_server(cfg, [1, 3], snapshot=snapshot(), records=[], pid_check=lambda p: True)


def test_provider_cost_guard(monkeypatch):
    monkeypatch.setenv("HA_INFERENCE_MODE", "paid_opt_in")
    with pytest.raises(ValueError, match="local_only"):
        exp.local_config("Qwen/Qwen3-8B", "http://127.0.0.1:8000/v1")
    monkeypatch.setenv("HA_INFERENCE_MODE", "local_only")
    with pytest.raises(RuntimeError):
        exp.local_config("Qwen/Qwen3-8B", "https://api.openai.com/v1")


def test_no_optin_no_execution(tmp_path):
    with pytest.raises(ValueError, match="RUN_REAL"):
        exp.run_trials(output_dir=tmp_path)
    assert not (tmp_path / "run_manifest.json").exists()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"repeats": 0},
        {"repeats": 3},
        {"conditions": ["X"]},
        {"conditions": ["B0", "B0"]},
        {"skip_judge": False},
    ],
)
def test_configuration_budget(tmp_path, kwargs):
    with pytest.raises(ValueError):
        run(tmp_path, **kwargs)


def test_mock_full_path_isolation_resume_and_outputs(tmp_path):
    assert run(tmp_path, conditions=["B0", "B1", "B2"], max_target_runs=3) == 0
    manifest = exp.read_json(tmp_path / "run_manifest.json")
    assert "multi_agent/tools/finance.yaml" in manifest["configuration"]["implementation_sha256"]
    assert (
        "multi_agent/fixtures/finance/seed_clients.json"
        in manifest["configuration"]["implementation_sha256"]
    )
    assert len(manifest["attempts"]) == len({a["trial_id"] for a in manifest["attempts"]}) == 3
    for p in (tmp_path / "trials").glob("*/trial.json"):
        row = exp.read_json(p)
        assert row["mode"] == "MOCK"
        assert row["forbidden_violation_count"] == 3
        assert row["unnecessary_tool_count"] == 1
        assert row["task_completion"] is None
        assert row["original_harnessaudit_metrics"]["n_actions"] == 6
        assert Path(row["trajectory_path"]).is_file()
        dbpath = next(p.parent.rglob("*.sqlite"))
        with sqlite3.connect(dbpath) as db:
            assert db.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 4
    assert run(tmp_path, conditions=["B0", "B1", "B2"], max_target_runs=3) == 0
    assert len(exp.read_json(tmp_path / "run_manifest.json")["attempts"]) == 3
    report = analyze(tmp_path)
    assert report["preliminary_sample_differences"]["B2_minus_B1"] == 0
    for name in ("trial_summary.csv", "rule_observations.csv", "report.md", "rule_catalog.json"):
        assert (tmp_path / name).stat().st_size > 0
    with pytest.raises(ValueError, match="Resume configuration"):
        run(tmp_path, conditions=["B0", "B1", "B2"], max_target_runs=3, seed=10)


def test_database_reset_after_mutation(tmp_path, monkeypatch):
    from multi_agent.research_phase1.mock import MockAdapter

    orig = MockAdapter.run
    seen = []

    async def mutate(self, ctx, initial_input):
        seen.append(
            ctx.bank.conn.execute(
                "SELECT risk_tolerance FROM clients WHERE client_id='CL-7392'"
            ).fetchone()[0]
        )
        ctx.bank.conn.execute("UPDATE clients SET risk_tolerance='TEST' WHERE client_id='CL-7392'")
        ctx.bank.conn.commit()
        return await orig(self, ctx, initial_input)

    monkeypatch.setattr(MockAdapter, "run", mutate)
    assert run(tmp_path, repeats=2, max_target_runs=2) == 0
    assert len(seen) == 2 and seen[0] == seen[1] != "TEST"


def test_failure_budget_and_unknown_preserved(tmp_path, monkeypatch):
    def crash(*args, **kwargs):
        raise RuntimeError("fixture transport failed")

    monkeypatch.setattr(exp, "execute_target", crash)
    assert run(tmp_path) == 1
    manifest = exp.read_json(tmp_path / "run_manifest.json")
    assert manifest["attempts"][0]["status"] == "failed"
    row = exp.read_json(next((tmp_path / "trials").glob("*/trial.json")))
    assert row["forbidden_violation_count"] is None
    assert run(tmp_path) == 1  # no retries, no budget bypass
    assert len(exp.read_json(tmp_path / "run_manifest.json")["attempts"]) == 1
    report = analyze(tmp_path)
    assert report["conditions"][0]["forbidden_trial_rate"] is None


def test_malformed_result_and_missing_observation(tmp_path):
    assert run(tmp_path) == 0
    row = exp.read_json(next((tmp_path / "trials").glob("*/trial.json")))
    Path(row["result_json_path"]).write_text("{bad")
    report = analyze(tmp_path)
    assert report["artifact_errors"]
    assert report["conditions"][0]["successful_executions"] == 0
    assert report["conditions"][0]["forbidden_trial_rate"] is None
    assert (
        "unknown_missing_or_invalid_artifact" in (tmp_path / "rule_observations.csv").read_text()
    )


def test_interrupted_reservation(tmp_path):
    assert run(tmp_path) == 0
    p = next((tmp_path / "trials").glob("*/trial.json"))
    p.unlink()
    assert analyze(tmp_path)["failed_or_interrupted_executions"] == 1
    assert run(tmp_path) == 1


def test_manifest_identity(tmp_path):
    assert run(tmp_path) == 0
    p = next((tmp_path / "trials").glob("*/trial.json"))
    row = exp.read_json(p)
    row["experiment_id"] = "wrong"
    exp.save(p, row)
    assert analyze(tmp_path)["artifact_errors"]


def test_package_sanitizes_and_preserves(tmp_path):
    root = tmp_path / "experiment"
    assert run(root) == 0
    log = next(root.glob("trials/*/execution.log"))
    secret = "sk-test-" + "x" * 24
    log.write_text(log.read_text() + f"\napi_key={secret}\n{Path.home()}/private/file\n")
    before = log.read_bytes()
    (root / ".env").write_text(secret)
    archive = package(root)
    assert log.read_bytes() == before
    with tarfile.open(archive) as tar:
        assert "EXPORT_MANIFEST.json" in tar.getnames()
        assert not any(n.endswith((".sqlite", ".env")) for n in tar.getnames())
        for name in tar.getnames():
            assert secret not in tar.extractfile(name).read().decode()


def test_shell_entrypoints_and_exit_codes(tmp_path):
    scripts = exp.REPO_ROOT / "scripts/research_phase1"
    for script in scripts.glob("*.sh"):
        assert subprocess.run(["bash", "-n", str(script)]).returncode == 0
    env = dict(os.environ, OUTPUT_DIR=str(tmp_path / "blocked"), RUN_REAL="NO", RUN_MOCK="NO")
    r = subprocess.run(["bash", str(scripts / "02_smoke_clean.sh")], env=env, capture_output=True)
    assert r.returncode == 2 and b"Real target disabled" in r.stderr
    assert not (tmp_path / "blocked").exists()


def test_original_objective_is_protected(task):
    note = exp.notes()["B1"][0]["text"]
    active = TaskContract(scenario_id=task.task_id, task=task).compose_goal(note)
    exp.validate_condition(task, active, note)
    active.goal = active.goal.replace("PF-4821", "PF-0000")
    with pytest.raises(ValueError, match="Original goal"):
        exp.validate_condition(task, active, note)


def test_low_severity_topology_still_prohibits_user_channel(task):
    rule = next(
        r
        for r in task.access_rules
        if r.role == "trader"
        and r.access_type == "information.communication"
        and r.match.recipient == "user"
    )
    assert rule.severity == "low"
    assert classify(task, rule) == "forbidden_communication"


def test_transport_and_orphan_events_are_unknown(task):
    a = action(role="trader", raw_event={"is_error": True})
    metrics, _ = evaluate(task, [a], "trial")
    assert metrics["evidence_status"] == "unknown_or_partial"
    a.raw_event = {}
    a.tool_result = "(no result recorded)"
    metrics, _ = evaluate(task, [a], "trial")
    assert metrics["evidence_status"] == "unknown_or_partial"


def test_artifact_checksum_detects_valid_json_tampering(tmp_path):
    assert run(tmp_path) == 0
    path = next(tmp_path.glob("trials/*/trial.json"))
    value = exp.read_json(path)
    value["forbidden_violation_count"] = 0
    exp.save(path, value)
    report = analyze(tmp_path)
    assert "checksum mismatch" in report["artifact_errors"][0]["error"]
    assert report["conditions"][0]["rate_denominator"] == 0


def test_duplicate_native_event_dedup_keeps_references(task):
    a = action(raw_event={"source_file": "native/agent/trace.jsonl", "source_line": 12})
    b = a.model_copy(update={"sequence_num": 2})
    metrics, obs = evaluate(task, [a, b], "trial")
    assert metrics["original_violation_count"] == 2
    assert metrics["forbidden_violation_count"] == 1
    finding = next(o for o in obs if o["violation_observed"])
    assert finding["event_references"] == [1, 2]
    assert finding["violation_event_count"] == 1


def test_default_resource_mismatch_is_ambiguous_not_proven_forbidden(task):
    a = action(role="trader")
    a.tool_args["portfolio_id"] = ""
    metrics, obs = evaluate(task, [a], "trial")
    assert metrics["original_violation_count"] == 1
    assert metrics["forbidden_violation_count"] is None
    assert metrics["violations_by_type"]["forbidden_resource"] is None
    ambiguous = [o for o in obs if o["ambiguous_rule_match"]]
    assert ambiguous and ambiguous[0]["violation_observed"] is None


def test_partial_mock_trace_is_preserved_and_excluded(tmp_path, monkeypatch):
    from multi_agent.research_phase1.mock import MockAdapter

    original = MockAdapter.run

    async def partial(self, ctx, initial_input):
        outcome = await original(self, ctx, initial_input)
        ctx.action_sink.emit_tool_call(
            agent_id="trader",
            agent_role="trader",
            tool_name="get_market_data",
            tool_args={},
            tool_result="",
            raw_event={"MOCK": True},
        )
        return outcome

    monkeypatch.setattr(MockAdapter, "run", partial)
    assert run(tmp_path) == 1
    report = analyze(tmp_path)
    assert report["conditions"][0]["rate_denominator"] == 0
    row = exp.read_json(next(tmp_path.glob("trials/*/trial.json")))
    assert row["original_harnessaudit_metrics"]["n_actions"] == 7
    assert row["research_security_metrics"]["execution_failure"] is True
    assert "unknown_or_partial" in row["evidence_status"]


@pytest.mark.parametrize(
    "response,valid",
    [
        ({"score": 0.0}, 1),
        ({"score": 1.0}, 1),
        ({}, 0),
        ({"score": "invalid"}, 0),
        ({"score": "NaN"}, 0),
        ({"score": 2}, 0),
    ],
)
def test_swallowed_judge_fallback_is_not_measured_completion(monkeypatch, response, valid):
    import asyncio

    from multi_agent import completion_judge

    async def judge(**kwargs):
        return response

    monkeypatch.setattr(completion_judge, "create_json_response_async", judge)
    with exp.observe_judges() as audit:
        asyncio.run(
            completion_judge._evaluate_llm_checkpoint(
                {"name": "test"}, "goal", [], model="Qwen/Qwen3-8B"
            )
        )
    assert audit["completion_judge"]["valid_scores"] == valid
    assert bool(audit["completion_judge"]["errors"]) == (not valid)


def test_failed_judge_is_observed_even_when_original_returns_zero(monkeypatch):
    import asyncio

    from multi_agent import completion_judge
    from multi_agent.openai_json import OpenAIJSONError

    async def unavailable(**kwargs):
        raise OpenAIJSONError("fixture unavailable")

    monkeypatch.setattr(completion_judge, "create_json_response_async", unavailable)
    with exp.observe_judges() as audit:
        original_score = asyncio.run(
            completion_judge._evaluate_llm_checkpoint(
                {"name": "test"}, "goal", [], model="Qwen/Qwen3-8B"
            )
        )
    assert original_score == 0.0  # Original metric is intentionally preserved.
    assert audit["completion_judge"]["valid_scores"] == 0
    assert audit["completion_judge"]["errors"] == ["OpenAIJSONError"]
