"""Cost-guard, configuration and GPU-placement tests (no GPU or server needed).

GPU scenarios use mocked NVML snapshots; cost-guard tests deliberately put
commercial-looking credentials in the environment to prove local_only mode
never selects them.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import threading
from pathlib import Path

import pytest

from multi_agent.local_inference import config as cfgmod
from multi_agent.local_inference.config import ROLES, load_config
from multi_agent.local_inference.endpoints import EndpointClass, classify_endpoint
from multi_agent.local_inference.gpu_allocator import (
    GpuReservations,
    PlacementPolicy,
    plan_placement,
)
from multi_agent.local_inference.gpu_discovery import discover, permitted_devices
from multi_agent.local_inference.model_registry import REGISTRY
from multi_agent.local_inference.router import (
    CostGuardError,
    check_attacker_backend,
    check_run_policy,
    credential_presence,
    require_local_route,
    resolve_route,
    scrubbed_child_env,
)

QWEN8 = REGISTRY["Qwen/Qwen3-8B"]
QWEN14 = REGISTRY["Qwen/Qwen3-14B"]
FAKE_KEYS = {
    "OPENAI_API_KEY": "sk-test-" + "x" * 40,
    "OPENAI_BASE_URL": "https://api.openai.com/v1",
    "OPENROUTER_API_KEY": "sk-or-v1-" + "y" * 40,
    "ANTHROPIC_API_KEY": "sk-ant-" + "z" * 40,
    "DASHSCOPE_API_KEY": "sk-" + "q" * 32,
}


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    for var in list(os.environ):
        if var.startswith(("HA_", "SLURM_")) or var in {"CUDA_VISIBLE_DEVICES",
                                                         "MASP_JUDGE_PROVIDER"}:
            monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HA_GPU_LOCK_DIR", str(tmp_path / "locks"))
    monkeypatch.setenv("HA_SERVER_STATE_DIR", str(tmp_path / "servers"))
    cfgmod._CACHED = None
    yield
    cfgmod._CACHED = None


def _cfg(*overrides: str, env: dict[str, str] | None = None):
    return load_config(overrides=list(overrides), env=env or {})


# ----------------------------------------------------------------- 10.1 cost safety


def test_default_config_is_local_only_and_all_routes_local():
    cfg = _cfg()
    assert cfg.inference.mode == "local_only"
    assert cfg.harness.default == "qwen_code"
    for role in ROLES:
        route = resolve_route(role, cfg)
        assert route.endpoint_class == "LOCAL" and not route.billable, role


def test_commercial_keys_in_env_are_never_selected(monkeypatch):
    for key, value in FAKE_KEYS.items():
        monkeypatch.setenv(key, value)
    cfg = _cfg()
    for role in ROLES:
        route = resolve_route(role, cfg)
        assert route.endpoint.startswith("http://127.0.0.1")
        assert route.api_key not in FAKE_KEYS.values()
    routes = check_run_policy(framework="clawteam", harness="qwen_code", model=None,
                              skip_judge=False, judge_model=None, config=cfg)
    assert {r.role for r in routes} >= {"target_coordinator", "completion_judge"}
    child = scrubbed_child_env(dict(os.environ))
    for key in FAKE_KEYS:
        assert child[key] == ""
    presence = credential_presence()
    assert "value hidden" in presence["OPENAI_API_KEY"]
    assert FAKE_KEYS["OPENAI_API_KEY"] not in json.dumps(presence)


def test_placeholder_keys_are_not_billing_warnings():
    presence = credential_presence({"OPENAI_API_KEY": "EMPTY", "OPENROUTER_API_KEY": "..."})
    assert presence == {"OPENAI_API_KEY": "placeholder/empty",
                        "OPENROUTER_API_KEY": "placeholder/empty"}


@pytest.mark.parametrize("harness", ["claude", "codex", "openclaw"])
def test_paid_harnesses_refused_in_local_only(harness):
    with pytest.raises(CostGuardError):
        check_run_policy(framework="clawteam", harness=harness, model=None,
                         skip_judge=True, judge_model=None, config=_cfg())


@pytest.mark.parametrize("framework", ["oai", "adk"])
def test_hosted_frameworks_refused_in_local_only(framework):
    with pytest.raises(CostGuardError):
        check_run_policy(framework=framework, harness=framework, model="gpt-5.4",
                         skip_judge=True, judge_model=None, config=_cfg())


def test_paid_harness_allowed_only_with_explicit_opt_in():
    cfg = _cfg("inference.mode=paid_opt_in")
    assert check_run_policy(framework="clawteam", harness="codex", model="gpt-5.4",
                            skip_judge=False, judge_model="gpt-5.4", config=cfg) == []
    check_attacker_backend("codex_cli", cfg)


def test_paid_fallback_flag_contradiction_rejected():
    with pytest.raises(CostGuardError):
        check_run_policy(framework="clawteam", harness="qwen_code", model=None,
                         skip_judge=True, judge_model=None,
                         config=_cfg("inference.allow_paid_fallback=true"))


@pytest.mark.parametrize("url,expected", [
    ("http://127.0.0.1:8000/v1", "LOCAL"),
    ("http://localhost:8000/v1", "LOCAL"),
    ("http://[::1]:8000/v1", "LOCAL"),
    ("https://api.openai.com/v1", "COMMERCIAL"),
    ("https://openrouter.ai/api/v1", "COMMERCIAL"),
    ("https://dashscope.aliyuncs.com/compatible-mode/v1", "COMMERCIAL"),
    ("https://my-resource.openai.azure.com/", "COMMERCIAL"),
    ("http://10.1.2.3:8000/v1", "UNKNOWN"),
    ("http://gpu-node07:8000/v1", "UNKNOWN"),
    ("ftp://127.0.0.1/v1", "UNKNOWN"),
])
def test_endpoint_classification(url, expected):
    assert classify_endpoint(url).value == expected


def test_unapproved_network_endpoint_fails_closed_until_approved():
    cfg = _cfg("routes.completion_judge.endpoint=http://gpu-node07:8000/v1")
    with pytest.raises(CostGuardError):
        require_local_route("completion_judge", cfg)
    cfg = _cfg("routes.completion_judge.endpoint=http://gpu-node07:8000/v1",
               'inference.approved_endpoints=["gpu-node07:8000"]')
    assert require_local_route("completion_judge", cfg).endpoint_class == \
        EndpointClass.SELF_HOSTED_APPROVED.value
    # approval can never whitelist a commercial host
    cfg = _cfg("routes.completion_judge.endpoint=https://api.openai.com/v1",
               'inference.approved_endpoints=["api.openai.com"]')
    with pytest.raises(CostGuardError):
        require_local_route("completion_judge", cfg)


def test_attacker_and_target_use_their_assigned_routes():
    cfg = _cfg("routes.attacker_generator.model=Qwen/Qwen3-4B",
               "routes.attacker_generator.endpoint=http://127.0.0.1:8001/v1",
               "routes.target_specialists.model=Qwen/Qwen3-14B",
               "routes.target_specialists.endpoint=http://127.0.0.1:8002/v1")
    att, tgt = resolve_route("attacker_generator", cfg), resolve_route("target_specialists", cfg)
    assert (att.model, att.endpoint) == ("Qwen/Qwen3-4B", "http://127.0.0.1:8001/v1")
    assert (tgt.model, tgt.endpoint) == ("Qwen/Qwen3-14B", "http://127.0.0.1:8002/v1")
    assert resolve_route("target_coordinator", cfg).model == "Qwen/Qwen3-8B"


def test_paid_attacker_backend_refused():
    with pytest.raises(CostGuardError):
        check_attacker_backend("codex_cli", _cfg())
    check_attacker_backend("mock", _cfg())


def test_judge_cannot_silently_use_hosted_model(monkeypatch):
    from multi_agent import openai_json

    created = []

    class Recorder:
        def __init__(self, **kwargs):
            created.append(kwargs)

    monkeypatch.setattr("openai.AsyncOpenAI", Recorder)
    monkeypatch.setattr("openai.OpenAI", Recorder)
    for key, value in FAKE_KEYS.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(CostGuardError):
        asyncio.run(openai_json.create_json_response_async(
            model="gpt-5.4", system_prompt="s", user_prompt="u"))
    assert created == []   # no client was even constructed


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_unavailable_local_judge_raises_without_hosted_fallback(monkeypatch):
    from multi_agent import openai_json

    port = _free_port()   # nothing listens here
    monkeypatch.setenv("HA_LOCAL_ENDPOINT", f"http://127.0.0.1:{port}/v1")
    monkeypatch.setenv("MASP_OPENAI_RETRY_ATTEMPTS", "1")
    for key, value in FAKE_KEYS.items():
        monkeypatch.setenv(key, value)
    seen = []
    import openai as openai_mod

    real = openai_mod.AsyncOpenAI

    def spy(**kwargs):
        seen.append(kwargs.get("base_url"))
        return real(**kwargs)

    monkeypatch.setattr("openai.AsyncOpenAI", spy)
    with pytest.raises(openai_json.LocalEndpointUnavailable):
        asyncio.run(openai_json.create_json_response_async(
            model="Qwen/Qwen3-8B", system_prompt="s", user_prompt="u"))
    assert seen == [f"http://127.0.0.1:{port}/v1"]


def test_missing_local_server_blocks_run_before_agents(monkeypatch):
    import argparse

    from multi_agent.cli import _enforce_inference_policy

    monkeypatch.setenv("HA_LOCAL_ENDPOINT", f"http://127.0.0.1:{_free_port()}/v1")
    args = argparse.Namespace(framework="clawteam", model=None, skip_judge=True,
                              judge_model=None)
    with pytest.raises(RuntimeError, match="not being served"):
        _enforce_inference_policy(args, "qwen_code")


def test_audit_report_lists_every_route_and_hides_secrets(monkeypatch):
    from multi_agent.local_inference.cost_audit import EXIT_CODES, build_report

    for key, value in FAKE_KEYS.items():
        monkeypatch.setenv(key, value)
    report = build_report(_cfg(), probe=False, attacker_backend="qwen_code")
    assert {r["role"] for r in report["routes"]} == set(ROLES)
    assert report["verdict"] in {"LOCAL_ONLY_STATIC_OK", "UNRESOLVED"}
    blob = json.dumps(report)
    for value in FAKE_KEYS.values():
        if value.startswith("sk-"):
            assert value not in blob
    bad = build_report(_cfg("routes.operational_judge.endpoint=https://openrouter.ai/api/v1"),
                       probe=False)
    assert bad["verdict"] == "POTENTIALLY_BILLABLE"
    assert EXIT_CODES[bad["verdict"]] != 0
    paid = build_report(_cfg("inference.mode=paid_opt_in"), probe=False)
    assert paid["verdict"] == "PAID_OPT_IN_ACTIVE" and EXIT_CODES[paid["verdict"]] != 0


def test_single_agent_refused_in_local_only():
    import argparse

    from single_agent.cli import cmd_run

    with pytest.raises(CostGuardError):
        cmd_run(argparse.Namespace(model="x", task="t"))


def test_qwen_runtime_child_env_is_local(monkeypatch, tmp_path):
    from multi_agent.frameworks.clawteam.runtime import HarnessRunContext
    from multi_agent.frameworks.native.qwen_code.runtime import QwenCodeHarnessRuntime

    fake_qwen = tmp_path / "qwen"
    fake_qwen.write_text("#!/bin/sh\n")
    fake_qwen.chmod(0o755)
    monkeypatch.setenv("HA_QWEN_BIN", str(fake_qwen))
    monkeypatch.setenv("MASP_QWEN_ISOLATION_ROOT", str(tmp_path / "iso"))
    for key, value in FAKE_KEYS.items():
        monkeypatch.setenv(key, value)
    rt = QwenCodeHarnessRuntime()
    rt.register_mcp(context=HarnessRunContext(
        run_id="r1", worktrees={"hub": tmp_path / "hub", "spoke": tmp_path / "spoke"},
        mcp_port=1234, clawteam_command="clawteam-mcp"))
    rt.register_agent_instructions("spoke", "You are the spoke.")
    env = rt.build_spawn_env(agent_role="spoke")
    assert env["OPENAI_BASE_URL"] == "http://127.0.0.1:8000/v1"
    assert env["OPENAI_API_KEY"] not in FAKE_KEYS.values()
    assert env["ANTHROPIC_API_KEY"] == "" and env["OPENROUTER_API_KEY"] == ""
    assert env["QWEN_USAGE_STATISTICS_ENABLED"] == "0"
    settings = json.loads((Path(env["QWEN_HOME"]) / "settings.json").read_text())
    assert settings["security"]["auth"]["selectedType"] == "openai"
    assert settings["privacy"]["usageStatisticsEnabled"] is False
    assert settings["memory"]["enableManagedAutoMemory"] is False
    cmd = rt.adjust_spawn_command(["qwen"])
    assert Path(cmd[0]).name == "qwen" and "--bare" in cmd and "--fallback-model" not in cmd
    assert cmd[cmd.index("--auth-type") + 1] == "openai"
    prompt = Path(env["HA_QWEN_SYSTEM_PROMPT_FILE"]).read_text()
    assert "You are the spoke." in prompt and "mcp__clawteam__task_list" in prompt
    mcp = json.loads(Path(env["HA_QWEN_MCP_CONFIG"]).read_text())
    assert mcp["mcpServers"]["multi_agent_tools"]["httpUrl"] == "http://127.0.0.1:1234/mcp"
    rt.cleanup()


# ----------------------------------------------------------------- configuration


def test_config_precedence(tmp_path):
    cfg_file = tmp_path / "c.yaml"
    cfg_file.write_text("inference:\n  port: 8100\n  max_num_seqs: 4\n"
                        "models:\n  attacker: Qwen/Qwen3-4B\n")
    env = {"HA_CONFIG": str(cfg_file), "HA_SERVER_PORT": "8200",
           "HA_SET": "inference.max_num_seqs=6"}
    cfg = load_config(env=env, overrides=["inference.max_num_seqs=7"])
    assert cfg.inference.port == 8200
    assert cfg.inference.max_num_seqs == 7
    assert resolve_route("attacker_generator", cfg).model == "Qwen/Qwen3-4B"
    assert resolve_route("target_specialists", cfg).model == "Qwen/Qwen3-8B"
    assert cfg.sources[0] == "defaults" and cfg.sources[-1] == "cli"


def test_reasoning_mode_on_off_overrides():
    cfg = load_config(env={"HA_SET": "routes.attacker_generator.reasoning_mode=on,"
                                     "inference.reasoning_mode=off"})
    assert resolve_route("attacker_generator", cfg).reasoning_mode == "on"
    assert resolve_route("completion_judge", cfg).reasoning_mode == "off"


def test_gpu_ids_env_implies_explicit_selection():
    cfg = load_config(env={"HA_GPU_IDS": "0,2,3"})
    assert cfg.gpu.selection == "explicit" and cfg.gpu.explicit_ids == [0, 2, 3]


@pytest.mark.parametrize("profile", ["l40_shared", "a100_single", "a100_multinode", "gh200"])
def test_cluster_profiles_load(profile):
    cfg = load_config(profile=profile, env={})
    assert cfg.inference.mode == "local_only"


def test_unknown_route_role_rejected():
    with pytest.raises(ValueError):
        load_config(overrides=["routes.not_a_role.model=x"], env={})


# ----------------------------------------------------------------- 10.2 GPU placement


def _dev(index, *, name="NVIDIA L40S", total=45.0, used=0.0, util=0.0, procs=None, mig=False,
         cc="8.9"):
    return {"index": index, "uuid": f"GPU-{index:04d}-uuid", "name": name, "total_gb": total,
            "free_gb": total - used, "used_gb": used, "utilization_pct": util,
            "compute_capability": cc, "mig_enabled": mig, "processes": procs or []}


def _foreign(gb=10.0):
    return [{"pid": 999999, "used_memory_gb": gb, "uid": 4242, "own": False}]


def _snapshot(monkeypatch, tmp_path, devices, **env):
    path = tmp_path / "gpus.json"
    path.write_text(json.dumps({"host": "testhost", "driver_version": "test",
                                "devices": devices}))
    monkeypatch.setenv("HA_GPU_MOCK_FILE", str(path))
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return discover()


def _plan(snap, spec=QWEN8, **policy):
    return plan_placement(snap, spec, PlacementPolicy(**policy))


def test_one_idle_gpu(monkeypatch, tmp_path):
    plan = _plan(_snapshot(monkeypatch, tmp_path, [_dev(0)]))
    assert plan.ok and plan.tensor_parallel_size == 1 and plan.tokens == ["0"]
    assert 0.05 < plan.gpu_memory_utilization <= 0.80


def test_four_idle_gpus_use_one_for_small_model(monkeypatch, tmp_path):
    plan = _plan(_snapshot(monkeypatch, tmp_path, [_dev(i) for i in range(4)]))
    assert plan.ok and plan.tensor_parallel_size == 1 and plan.replicas == 1
    assert len(plan.tokens) == 1


def test_all_gpus_heavily_occupied(monkeypatch, tmp_path):
    devs = [_dev(i, used=40.0, util=95.0, procs=_foreign(40.0)) for i in range(4)]
    plan = _plan(_snapshot(monkeypatch, tmp_path, devs), allow_shared_gpu=True)
    assert not plan.ok and "Insufficient" in plan.errors[0]


def test_gpu0_occupied_gpu2_available(monkeypatch, tmp_path):
    devs = [_dev(0, used=30.0, util=60.0, procs=_foreign(30.0)),
            _dev(1, used=35.0, util=90.0, procs=_foreign(35.0)),
            _dev(2), _dev(3, used=20.0, util=10.0, procs=_foreign(20.0))]
    plan = _plan(_snapshot(monkeypatch, tmp_path, devs))
    assert plan.ok and plan.tokens == ["2"]


def test_free_memory_but_high_utilization_is_excluded(monkeypatch, tmp_path):
    devs = [_dev(0, used=40.0, procs=_foreign(40.0)), _dev(1, util=85.0)]
    plan = _plan(_snapshot(monkeypatch, tmp_path, devs), allow_shared_gpu=True)
    assert not plan.ok
    reasons = {c.index: " ".join(c.reasons) for c in plan.candidates}
    assert "utilization" in reasons[1]


def test_shared_gpu_only_with_permission(monkeypatch, tmp_path):
    devs = [_dev(0, used=12.0, util=10.0, procs=_foreign(12.0))]
    snap = _snapshot(monkeypatch, tmp_path, devs)
    assert not _plan(snap).ok
    plan = _plan(snap, allow_shared_gpu=True)
    assert plan.ok and plan.tokens == ["0"]
    # budget respects other users' memory plus the safety margin
    assert plan.gpu_memory_utilization <= (45.0 - 12.0 - 6.0) / 45.0 + 1e-9


def test_explicit_noncontiguous_ids_never_use_gpu1(monkeypatch, tmp_path):
    snap = _snapshot(monkeypatch, tmp_path, [_dev(i) for i in range(4)])
    plan = _plan(snap, selection="explicit", explicit_ids=[0, 2, 3],
                 data_parallel_replicas="auto")
    assert plan.ok and plan.replicas == 3 and plan.tensor_parallel_size == 1
    assert set(plan.tokens) == {"0", "2", "3"} and "1" not in plan.tokens
    assert [d["nvidia_smi_index"] for d in plan.device_map] == [0, 2, 3]
    assert [d["cuda_device"] for d in plan.device_map] == ["cuda:0", "cuda:1", "cuda:2"]
    single = _plan(snap, selection="explicit", explicit_ids=[0, 2, 3])
    assert single.ok and len(single.tokens) == 1 and single.tokens[0] in {"0", "2", "3"}
    assert any("left unused" in w for w in single.warnings)


def test_three_gpus_never_become_invalid_tp3(monkeypatch, tmp_path):
    snap = _snapshot(monkeypatch, tmp_path, [_dev(i, total=32.0) for i in range(4)])
    plan = _plan(snap, spec=QWEN14, selection="explicit", explicit_ids=[0, 2, 3])
    assert plan.ok and plan.tensor_parallel_size == 2 and len(plan.tokens) == 2
    bad = _plan(snap, spec=QWEN8, selection="explicit", explicit_ids=[0, 2, 3],
                tensor_parallel_size=3)
    assert not bad.ok and "invalid" in bad.errors[0]


def test_insufficient_memory(monkeypatch, tmp_path):
    plan = _plan(_snapshot(monkeypatch, tmp_path, [_dev(0, total=16.0)]))
    assert not plan.ok and "Insufficient GPU memory" in plan.errors[0]


def test_no_gpus_detected(monkeypatch, tmp_path):
    plan = _plan(_snapshot(monkeypatch, tmp_path, []))
    assert not plan.ok and "No permitted GPUs" in plan.errors[0]


def test_cuda_visible_devices_restricts_and_maps(monkeypatch, tmp_path):
    snap = _snapshot(monkeypatch, tmp_path, [_dev(i) for i in range(4)],
                     CUDA_VISIBLE_DEVICES="2,3")
    permitted, _ = permitted_devices(snap)
    assert [p.token for p in permitted] == ["2", "3"]
    assert not _plan(snap, selection="explicit", explicit_ids=[1]).ok
    plan = _plan(snap)
    assert plan.ok and plan.tokens[0] in {"2", "3"}


def test_cgroup_renumbered_visible_devices(monkeypatch, tmp_path):
    # Inside a cgroup only the allocated GPU is visible to NVML, renumbered as 0,
    # while the parent CUDA_VISIBLE_DEVICES still says "3".
    snap = _snapshot(monkeypatch, tmp_path, [_dev(0)], CUDA_VISIBLE_DEVICES="3")
    permitted, _ = permitted_devices(snap)
    assert permitted[0].token == "3" and permitted[0].device is not None


def test_slurm_allocation_permits_only_one_gpu(monkeypatch, tmp_path):
    snap = _snapshot(monkeypatch, tmp_path, [_dev(i) for i in range(4)],
                     SLURM_JOB_ID="123", SLURM_JOB_NODELIST="gpu[01-02]",
                     SLURM_JOB_NUM_NODES="2", SLURM_STEP_GPUS="1")
    assert snap.slurm.nodelist == ["gpu01", "gpu02"] and snap.slurm.num_nodes == 2
    plan = _plan(snap, selection="scheduler")
    assert plan.ok and plan.tokens == ["1"]
    snap_none = _snapshot(monkeypatch, tmp_path, [_dev(i) for i in range(4)],
                          SLURM_JOB_ID="124", SLURM_STEP_GPUS="")
    monkeypatch.delenv("SLURM_STEP_GPUS")
    snap_none = discover()
    plan = _plan(snap_none, selection="scheduler")
    assert not plan.ok and "no GPUs" in plan.errors[0]


def test_mixed_gpu_models_tp_groups_are_homogeneous(monkeypatch, tmp_path):
    devs = [_dev(0, name="NVIDIA A100-SXM4-40GB", total=40.0),
            _dev(1, name="NVIDIA A100-SXM4-80GB", total=80.0),
            _dev(2, name="NVIDIA A100-SXM4-40GB", total=40.0)]
    snap = _snapshot(monkeypatch, tmp_path, devs)
    plan = _plan(snap, spec=QWEN14, tensor_parallel_size=2)
    assert plan.ok
    names = {d["name"] for d in plan.device_map}
    assert len(names) == 1


def test_mig_devices_are_not_auto_selected(monkeypatch, tmp_path):
    snap = _snapshot(monkeypatch, tmp_path, [_dev(0, mig=True), _dev(1)])
    plan = _plan(snap)
    assert plan.ok and plan.tokens == ["1"]
    snap = _snapshot(monkeypatch, tmp_path, [_dev(0, mig=True)],
                     CUDA_VISIBLE_DEVICES="MIG-aaaa-bbbb")
    plan = _plan(snap)
    assert not plan.ok   # MIG instance memory is not verifiable -> fail closed


def test_placement_race_between_two_project_launches(monkeypatch, tmp_path):
    snap = _snapshot(monkeypatch, tmp_path, [_dev(0), _dev(1)])
    res = GpuReservations(tmp_path / "locks", host="testhost")
    winners: list[str] = []
    barrier = threading.Barrier(2)

    def launch(label: str) -> None:
        barrier.wait()
        with res.placement_lock():
            plan = plan_placement(snap, QWEN8, PlacementPolicy(), reserved=res.active())
            assert plan.ok
            key = plan.device_map[0]["uuid"]
            res.reserve([key], label=label)
            winners.append(key)

    threads = [threading.Thread(target=launch, args=(f"L{i}",)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(winners) == ["GPU-0000-uuid", "GPU-0001-uuid"]   # no double booking
    with pytest.raises(RuntimeError):
        res.reserve(["GPU-0000-uuid"], label="third")


def test_stale_reservation_is_reclaimed(monkeypatch, tmp_path):
    res = GpuReservations(tmp_path / "locks", host="testhost")
    res.reserve(["GPU-0000-uuid"], label="dead", pid=2**22 + 12345)  # no such pid
    assert res.active() == {}


def test_oom_during_startup_retries_smaller_and_preserves_logs(monkeypatch, tmp_path):
    """A fake vLLM that always OOMs: we stop our own process, keep every log, retry
    with smaller settings, and never touch other processes."""
    from multi_agent.local_inference import server

    _snapshot(monkeypatch, tmp_path, [_dev(0)])
    fake = tmp_path / "bin" / "vllm"
    fake.parent.mkdir()
    flags = ["--host", "--port", "--served-model-name", "--max-model-len",
             "--gpu-memory-utilization", "--max-num-seqs", "--tensor-parallel-size",
             "--pipeline-parallel-size", "--dtype", "--enable-auto-tool-choice",
             "--tool-call-parser", "--default-chat-template-kwargs", "--seed",
             "--reasoning-parser", "--max-num-batched-tokens", "--enforce-eager",
             "--override-generation-config"]
    help_text = "\n".join(f"  {f} X" for f in flags)
    fake.write_text(
        "#!/usr/bin/env bash\n"
        f"if [[ \"$2\" == --help=all ]]; then cat <<'EOF'\n{help_text}\nEOF\nexit 0; fi\n"
        "echo \"args: $*\"; echo 'torch.OutOfMemoryError: CUDA out of memory'; exit 1\n")
    fake.chmod(0o755)
    monkeypatch.setenv("HA_VLLM_BIN", str(fake))
    cfg = _cfg(f"inference.port={_free_port()}", "inference.startup_timeout_s=30")
    monkeypatch.setattr(server, "local_snapshot", lambda *a, **k: tmp_path)
    result = server.launch(cfg, name="oomtest", reuse=False, log_dir=tmp_path / "logs",
                           max_retries=2)
    assert not result.ok and len(result.attempts) == 3
    assert all(a["failure"] == "oom" for a in result.attempts)
    assert "--enforce-eager" in result.attempts[-1]["command"]
    seqs = [int(a["command"][a["command"].index("--max-num-seqs") + 1])
            for a in result.attempts]
    assert seqs[0] > seqs[1] > seqs[2]
    assert all(Path(a["log"]).exists() for a in result.attempts)
    assert GpuReservations().active() == {}   # our reservations were released
    assert server.list_servers() == []


# ----------------------------------------------------------------- ClawTeam waiter


def test_waiter_no_longer_steals_hub_mailbox(monkeypatch, tmp_path):
    """Regression: the runner's TaskWaiter used to consume the hub's inbox."""
    monkeypatch.setenv("CLAWTEAM_DATA_DIR", str(tmp_path / "ct"))
    from clawteam.store.file import FileTaskStore
    from clawteam.team.mailbox import MailboxManager
    from clawteam.team.manager import TeamManager

    from multi_agent.frameworks.clawteam.adapter import ClawTeamAdapter

    def run(preserve: bool, team: str) -> list[str]:
        TeamManager.create_team(name=team, leader_name="hub", leader_id="h", description="t")
        store = FileTaskStore(team)
        sentinel = store.create(subject="s", description="d", owner="hub")
        MailboxManager(team).send(from_agent="spoke", to="hub", content="REPORT")
        asyncio.run(ClawTeamAdapter._wait_for_team_runtime(
            team, hub_role="hub", sentinel_task_id=sentinel.id, timeout=1,
            preserve_hub_inbox=preserve))
        return [m.content for m in MailboxManager(team).receive("hub")]

    assert run(False, "legacy-team") == []          # historical behaviour: report lost
    assert run(True, "fixed-team") == ["REPORT"]     # fixed: hub agent still gets it
    monkeypatch.setenv("MASP_PRESERVE_HUB_INBOX", "")
    assert ClawTeamAdapter(harness="qwen_code")._preserve_hub_inbox() is True
    assert ClawTeamAdapter(harness="codex")._preserve_hub_inbox() is False


def test_task_fingerprint_is_independent_of_hash_seed():
    """Regression: rule order depended on str hash randomization (set iteration)."""
    import subprocess
    import sys

    code = ("from multi_agent.attacks.gateway import load_contract as l; print(l("
            "'multi_agent/tasks/finance/financial_planning/fin-t1.yaml', 4000)"
            ".protected_fingerprint())")
    prints = {
        subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       env={**os.environ, "PYTHONHASHSEED": str(seed)},
                       check=True).stdout.strip()
        for seed in (1, 2, 3, 4)
    }
    assert len(prints) == 1
