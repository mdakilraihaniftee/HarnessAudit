"""Supervised launcher tests. Local HTTP fixtures only; no GPUs or real models."""

from __future__ import annotations

import concurrent.futures
import http.client
import json
import os
import tarfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from multi_agent.research_phase1 import experiment as exp
from multi_agent.research_phase1.cohort_bundle import package_review, report
from multi_agent.research_phase1.pilot import clean_worker_env, load_ledger, smoke_usable
from multi_agent.research_phase1.request_proxy import RequestBudget, model_gateway
from multi_agent.research_phase1.review_bundle import descriptive, validate, wilson
from multi_agent.research_phase1.security import catalog


@pytest.fixture(autouse=True)
def env(monkeypatch):
    for name in list(os.environ):
        if name.startswith(("HA_", "MASP_", "SLURM_")) or name in {
            "RUN_REAL",
            "CUDA_VISIBLE_DEVICES",
        }:
            monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("k,n", [(0, 5), (5, 5), (2, 5), (0, 0)])
def test_wilson(k, n):
    interval = wilson(k, n)
    if n:
        assert 0 <= interval[0] <= k / n <= interval[1] <= 1
        assert interval[1] - interval[0] > 0
    else:
        assert interval is None


def test_uncertainty_and_unknown_denominators():
    rows = [
        {
            "condition": "B0",
            "execution_status": "success",
            "evidence_status": "complete_observed_trace",
            "forbidden_violation_count": 0,
        },
        {"condition": "B1", "execution_status": "failed"},
        {
            "condition": "B2",
            "execution_status": "success",
            "evidence_status": "complete_observed_trace",
            "forbidden_violation_count": 2,
        },
    ]
    result = descriptive(rows)
    assert result["conditions"]["B0"]["wilson_95_interval"][1] > 0
    assert result["conditions"]["B1"]["forbidden_trial_rate"] is None
    assert (
        result["preliminary_excess_risk"]["B2_minus_B1"]["preliminary_sample_difference"] is None
    )


def test_request_budget_atomic_durable_and_no_replacements(tmp_path):
    path = tmp_path / "requests.json"
    budget = RequestBudget(path, 12, 5)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(budget.reserve, ["B0-0"] * 20))
    assert sum(r is not None for r in rows) == 5
    loaded = RequestBudget(path, 12, 5)
    assert loaded.reserve("B0-0") is None
    assert loaded.state["used"] == 5
    with pytest.raises(ValueError):
        RequestBudget(path, 13, 5)


def test_ledger_resume_and_frozen_schedule(tmp_path):
    cfg = {"repeats": 5, "seed": 123, "mode": "MOCK"}
    ledger = load_ledger(tmp_path, cfg)
    assert ledger["schedule"][0] == ["B0", 0]
    assert len(ledger["schedule"]) == len(set(map(tuple, ledger["schedule"]))) == 15
    assert load_ledger(tmp_path, cfg)["pilot_id"] == ledger["pilot_id"]
    with pytest.raises(ValueError, match="differs"):
        load_ledger(tmp_path, dict(cfg, seed=456))


def test_child_cost_env_hides_paid_credentials(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-sensitive")
    monkeypatch.setenv("HA_ROUTE_COMPLETION_JUDGE_ENDPOINT", "https://api.openai.com/v1")
    monkeypatch.setenv("MAX_PARALLEL_TRIALS", "4")
    child = clean_worker_env()
    assert child["OPENAI_API_KEY"] == ""
    assert "HA_ROUTE_COMPLETION_JUDGE_ENDPOINT" not in child
    assert child["HA_INFERENCE_MODE"] == "local_only"
    assert child["HF_HUB_OFFLINE"] == "1"
    assert "MAX_PARALLEL_TRIALS" not in child


def test_explicit_note_offset_and_old_experiments_preserved(tmp_path):
    root = tmp_path / "child"
    assert exp.run_trials(output_dir=root, mock=True, conditions=["B1"], note_offset=4) == 0
    row = exp.read_json(next(root.glob("trials/*/trial.json")))
    assert row["note_id"] == exp.notes()["B1"][1]["id"]
    assert row["mode"] == "MOCK"


def test_review_bundle_cpu_validation_and_corruption(tmp_path):
    root = tmp_path / "pilot"
    root.mkdir()
    cfg = {"mode": "MOCK", "repeats": 5, "seed": 0, "model_request_budget": 6000}
    ledger = load_ledger(root, cfg)
    ledger["attempts"].append({"slot": "B0-0", "condition": "B0", "reservation_id": "reserved"})
    task, _ = exp.load_task_with_tools(exp.approved_task("fin-t1"))
    exp.save(root / "rule_catalog.json", {"rules": catalog(task)})
    assert exp.run_trials(output_dir=root / "executions/B0-0", mock=True) == 0
    assert smoke_usable(root, ledger, True)
    status = report(root, ledger)
    assert status["actual_target_executions_completed"] == 0
    assert not status["all_15_real_targets_completed"]
    archive = package_review(root, ledger)
    out = tmp_path / "export"
    with tarfile.open(archive) as tar:
        assert not any(p.endswith((".sqlite", ".env")) for p in tar.getnames())
        tar.extractall(out, filter="data")
    assert validate(out)["validation"] == "PASS"
    (out / "report.json").write_text("{}")
    with pytest.raises(ValueError, match="checksum"):
        validate(out)


def test_missing_worker_is_unknown_not_zero(tmp_path):
    root = tmp_path
    cfg = {"mode": "REAL_LOCAL", "repeats": 5, "seed": 0, "model_request_budget": 6000}
    ledger = load_ledger(root, cfg)
    ledger["attempts"].append({"slot": "B0-0", "condition": "B0", "reservation_id": "missing"})
    task, _ = exp.load_task_with_tools(exp.approved_task("fin-t1"))
    exp.save(root / "rule_catalog.json", {"rules": catalog(task)})
    status = report(root, ledger)
    assert status["actual_target_executions_started"] == 0
    assert status["descriptive_statistics"]["conditions"]["B0"]["forbidden_trial_rate"] is None
    assert len(exp.read_json(root / "datasets/rule_observations.json")["observations"]) == 39


def test_gateway_streaming_paths_redirects_and_limit(tmp_path):
    seen = []

    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            seen.append((self.path, self.headers.get("Authorization")))
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(b'data: {"choices":[]}\n\ndata: [DONE]\n\n')

        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", "https://api.openai.com")
            self.end_headers()

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    budget = RequestBudget(tmp_path / "budget.json", 1, 1)
    try:
        with model_gateway(f"http://127.0.0.1:{upstream.server_port}/v1", 0, budget) as endpoint:
            port = int(endpoint.split(":")[2].split("/")[0])
            conn = http.client.HTTPConnection("127.0.0.1", port)
            conn.request(
                "POST",
                "/slot/B2-0/v1/chat/completions",
                b"{}",
                {"Authorization": "Bearer PRIVATE"},
            )
            response = conn.getresponse()
            assert response.status == 200 and b"[DONE]" in response.read()
            conn.close()
            assert seen == [("/v1/chat/completions", "Bearer EMPTY")]
            conn = http.client.HTTPConnection("127.0.0.1", port)
            conn.request("POST", "/slot/B2-0/v1/chat/completions", b"{}")
            response = conn.getresponse()
            assert response.status == 429
            response.read()
            conn.close()
            conn = http.client.HTTPConnection("127.0.0.1", port)
            conn.request("GET", "/v1/models")
            response = conn.getresponse()
            assert response.status == 502
            response.read()
            conn.close()
        assert budget.state["used"] == 1
    finally:
        upstream.shutdown()
        upstream.server_close()
        thread.join(timeout=5)


def test_nonlocal_gateway_refused(tmp_path):
    budget = RequestBudget(tmp_path / "budget.json", 1, 1)
    with pytest.raises(ValueError, match="local"):
        with model_gateway("https://api.openai.com/v1", 0, budget):
            pass


def _live_snapshot(occupied=False):
    from multi_agent.local_inference.gpu_discovery import GpuDevice, GpuProcess, GpuSnapshot

    devices = [
        GpuDevice(
            i,
            f"GPU-{i}",
            "L40S",
            46,
            28 if occupied else 45,
            18 if occupied else 1,
            0,
            processes=[GpuProcess(12345, 17, os.getuid(), True)] if occupied else [],
        )
        for i in range(4)
    ]
    return GpuSnapshot("host", "nvml", "driver", devices, None, None)


def test_occupied_same_user_gpu_still_blocks_new_server(tmp_path, monkeypatch):
    from multi_agent.local_inference import gpu_discovery, server
    from multi_agent.research_phase1.pilot import start_server

    monkeypatch.setattr(gpu_discovery, "discover", lambda: _live_snapshot(True))
    monkeypatch.setattr(server, "endpoint_ready", lambda *a: False)

    def forbidden_launch(*a, **kw):
        pytest.fail("Busy GPU must not be used")

    monkeypatch.setattr(server, "launch", forbidden_launch)
    cfg = exp.local_config("Qwen/Qwen3-8B", "http://127.0.0.1:8000/v1", 1)
    with pytest.raises(ValueError, match="occupied"):
        start_server(tmp_path, cfg, [0, 1, 2, 3])


def test_verified_server_reuse_is_never_owned(tmp_path, monkeypatch):
    from multi_agent.local_inference import gpu_discovery, model_registry, server
    from multi_agent.research_phase1 import pilot

    cfg = exp.local_config("Qwen/Qwen3-8B", "http://127.0.0.1:8000/v1", 1)
    checkpoint = Path("/cached/revision")
    state = {
        "replicas": 4,
        "tensor_parallel_size": 1,
        "command": [
            "vllm",
            "serve",
            str(checkpoint),
            "--max-model-len",
            str(cfg.inference.context_length),
            "--max-num-seqs",
            str(cfg.inference.max_num_seqs),
            "--override-generation-config",
            json.dumps({"temperature": 0.7, "top_p": 0.8, "top_k": 20}),
            "--default-chat-template-kwargs",
            json.dumps({"enable_thinking": False}),
        ],
    }
    monkeypatch.setattr(gpu_discovery, "discover", lambda: _live_snapshot())
    monkeypatch.setattr(server, "endpoint_ready", lambda *a: True)
    monkeypatch.setattr(model_registry, "local_snapshot", lambda *a: checkpoint)
    monkeypatch.setattr(
        pilot,
        "verify_server",
        lambda *a: {"actual_server_gpu_ids": [0, 1, 2, 3], "server_record": state},
    )
    assert pilot.start_server(tmp_path, cfg, [0, 1, 2, 3]) is None
    assert not (tmp_path / "owned_server.json").exists()
    assert (tmp_path / "server_verification.json").exists()
