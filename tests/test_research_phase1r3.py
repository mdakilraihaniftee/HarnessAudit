"""R3 guidance, immutable provenance and smoke-gated 15-per-condition launch."""

import hashlib
import os
import re
import subprocess
import sys
import tarfile
from collections import Counter
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from multi_agent.research_phase1r import (
    audit,
    controls,
    diagnostic,
    prompt_repair,
    release,
    target,
)
from multi_agent.research_phase1r import (
    workflow_repair as repair,
)
from multi_agent.research_phase1r.workflow_review import review_trial


def test_guidance_is_general_and_r2_prompts_are_preserved(tmp_path):
    text = repair.COMMON + " ".join(repair.ROLES.values())
    assert not re.search(r"\d|CL-|PF-|NVDA|MSFT|AAPL|Conservative|max_position_pct", text)
    for hidden in ["access_rules", "checker", "V-OT", "core_completion", "completion_status"]:
        assert hidden not in text
    assert "current value" in text and "direction explicitly requested" in text
    assert "No active Goal" not in text  # Guidance explains the condition, not a canned result.
    assert "update_goal" in text and "final text response" in text
    assert "pure coordinator" in repair.ROLES["relationship_manager"]
    active, cfg = controls.task(), diagnostic.configuration([0, 1, 2, 3])
    pairs = release.prompt_snapshots(active, cfg, tmp_path)
    original = audit.read(
        target.REPO / "results/research_phase1r/target_v1r2/TARGET_V1R2.json", {}
    )
    if original:
        for role in prompt_repair.ROLES:
            assert (
                pairs["B0"][role + ".system"]["after_sha256"]
                == original["effective_agent_system_prompts"][role]
            )


@pytest.mark.parametrize("role", list(repair.ROLES))
def test_r3_runtime_delivers_scope_and_completion_guidance_without_tool_enforcement(
    tmp_path, role
):
    agent = next(a for a in controls.task().agents if a.role == role)
    runtime = target.RepairedRuntime(
        {role: "cap"}, target_version=repair.VERSION, prompt_evidence_dir=tmp_path / "diffs"
    )
    runtime._root = tmp_path
    runtime._mcp_template = {
        "mcpServers": {"multi_agent_tools": {"httpUrl": "http://127.0.0.1:1/mcp"}}
    }
    runtime._routes = {"target_specialists": object()}
    runtime.register_agent_instructions(role, agent.system_prompt)
    with patch.object(runtime, "_settings_for", return_value={}):
        files = runtime._prepare_agent(role, "target_specialists")
    after = files.system_prompt.read_text()
    assert repair.COMMON in after and repair.ROLES[role] in after
    assert agent.system_prompt in after
    before = (tmp_path / "diffs" / f"{role}.system.before.md").read_text()
    assert before == runtime._compose_system_prompt(
        prompt_repair.role_instructions(role, agent.system_prompt)
    )
    diff = (tmp_path / "diffs" / f"{role}.system.diff").read_text()
    assert "TARGET_V1R2/" in diff and "TARGET_V1R3/" in diff
    config = audit.read(files.mcp_config)
    assert config["mcpServers"]["multi_agent_tools"]["httpUrl"].endswith("?identity=cap")
    assert not any(k in str(config) for k in ["allowedTools", "excludedTools", "blockedTools"])


def test_r3_prompt_pairs_are_condition_identical_and_ignore_hidden_fields(tmp_path):
    active, cfg = controls.task(), diagnostic.configuration([0, 1, 2, 3])
    first = release.prompt_snapshots(active, cfg, tmp_path / "first", version=repair.VERSION)
    altered = active.model_copy(deep=True)
    altered.access_rules[0].description = "PRIVATE_AUDIT_SENTINEL"
    altered.completion_checkpoints[0]["rubric"] = "PRIVATE_ANSWER_SENTINEL"
    second = release.prompt_snapshots(altered, cfg, tmp_path / "second", version=repair.VERSION)
    assert first == second
    for role in repair.ROLES:
        assert len({first[arm][role + ".system"]["after_sha256"] for arm in first}) == 1
        for arm in first:
            assert (
                first[arm][role + ".user"]["before_sha256"]
                == first[arm][role + ".user"]["after_sha256"]
            )
            if role != "relationship_manager":
                text = (tmp_path / "second" / arm / f"{role}.assignment.after.md").read_text()
                assert "CL-7392" in text and "PF-4821" in text
                assert (
                    first[arm][role + ".assignment"]["before_sha256"]
                    == first[arm][role + ".assignment"]["after_sha256"]
                )
    assert all("PRIVATE_" not in p.read_text() for p in (tmp_path / "second").rglob("*.md"))


def test_r3_release_binds_prompts_and_retains_original_contract_and_model(tmp_path):
    cfg = diagnostic.configuration([0, 1, 2, 3])
    manifest = release.prepare_target(tmp_path, cfg, version=repair.VERSION)
    prior = target.target_identity(controls.task(), cfg, version=prompt_repair.VERSION)
    for key in ["task_sha256", "inference_configuration", "model_checkpoint", "model_revision"]:
        assert manifest[key] == prior[key]
    assert manifest["target_identity"] != prior["target_identity"]
    assert manifest["approved_diagnostic_configuration"]["repeats"] == 15
    assert manifest["approved_diagnostic_configuration"]["parallel"] == 1
    assert manifest["approved_diagnostic_configuration"]["replace_invalid_trials"] is False
    assert release.prepare_target(tmp_path, cfg, version=repair.VERSION) == manifest
    for name, expected in manifest["source_sha256"].items():
        assert hashlib.sha256((tmp_path / "source" / name).read_bytes()).hexdigest() == expected
    with patch.dict(repair.ROLES, {"trader": repair.ROLES["trader"] + " Report uncertainties."}):
        assert (
            target.target_identity(controls.task(), cfg, version=repair.VERSION)["target_identity"]
            != manifest["target_identity"]
        )
        with pytest.raises(ValueError, match="Frozen TARGET_V1R3"):
            release.prepare_target(tmp_path, cfg, version=repair.VERSION)


@pytest.mark.parametrize(
    "command,limits", [("smoke", repair.SMOKE_LIMITS), ("diagnostic", repair.STUDY_LIMITS)]
)
def test_r3_authorized_limits(command, limits):
    diagnostic.approved_limits(SimpleNamespace(command=command, **limits))


@pytest.mark.parametrize(
    "changed",
    [
        dict(repeats=10),
        dict(repeats=16),
        dict(parallel=2),
        dict(agent_timeout=301),
        dict(max_turns=31),
        dict(per_trial_requests=401),
        dict(model_request_budget=20000),
    ],
)
def test_r3_rejects_unapproved_configuration_before_gpu(changed):
    args = SimpleNamespace(
        command="diagnostic", target_version=repair.VERSION, **dict(repair.STUDY_LIMITS, **changed)
    )
    with patch.dict(os.environ, RUN_REAL="YES"), patch.object(diagnostic, "preflight") as pre:
        with pytest.raises(ValueError, match="approved 15-per-condition"):
            diagnostic.run(args)
    pre.assert_not_called()


@pytest.mark.parametrize(
    "smoke",
    [
        None,
        dict(passed=False, criteria={"clean_core": False}),
        dict(passed=True, target_identity="TARGET_V1R2:other"),
    ],
)
def test_15_per_condition_requires_matching_passed_smoke_before_gpu(tmp_path, smoke):
    manifest = tmp_path / "TARGET_V1R3.json"
    audit.write(manifest, dict(target_identity="TARGET_V1R3:frozen"))
    if smoke is not None:
        audit.write(tmp_path / "smoke/smoke_acceptance.json", smoke)
    args = SimpleNamespace(
        command="diagnostic",
        target_version=repair.VERSION,
        target_manifest=manifest,
        smoke_results_dir=tmp_path / "smoke",
        **repair.STUDY_LIMITS,
    )
    with patch.dict(os.environ, RUN_REAL="YES"), patch.object(diagnostic, "preflight") as pre:
        with pytest.raises(ValueError, match="Smoke"):
            diagnostic.run(args)
    pre.assert_not_called()


def test_r3_cannot_start_concurrency_benchmark():
    with patch.dict(os.environ, RUN_REAL="YES"), patch.object(diagnostic, "preflight") as pre:
        with pytest.raises(ValueError, match="gated benign diagnostics only"):
            diagnostic.run(SimpleNamespace(command="benchmark", target_version=repair.VERSION))
    pre.assert_not_called()


def test_accepted_diagnostic_reserves_45_unique_seeded_trials_after_matching_smoke(tmp_path):
    cfg = diagnostic.configuration([0, 1, 2, 3])
    manifest = target.target_identity(controls.task(), cfg, version=repair.VERSION)
    audit.write(tmp_path / "TARGET_V1R3.json", manifest)
    audit.write(
        tmp_path / "smoke/smoke_acceptance.json",
        dict(passed=True, target_identity=manifest["target_identity"]),
    )
    for name in ["offline_controls.json", "scripted_controls.json"]:
        audit.write(tmp_path / "controls" / name, dict(passed=True))
    root = tmp_path / "research_phase1r/diagnostic"
    args = SimpleNamespace(
        command="diagnostic",
        target_version=repair.VERSION,
        target_manifest=tmp_path / "TARGET_V1R3.json",
        smoke_results_dir=tmp_path / "smoke",
        results_dir=root,
        controls_dir=tmp_path / "controls",
        gpu_ids="0,1,2,3",
        server_port=8000,
        proxy_port=18081,
        **repair.STUDY_LIMITS,
    )
    with (
        patch.dict(os.environ, RUN_REAL="YES"),
        patch.object(diagnostic, "preflight", return_value={}),
        patch.object(
            diagnostic, "tracked_model_server", side_effect=RuntimeError("CPU stub: no model")
        ) as server,
        patch("multi_agent.research_phase1r.reporting.analyze"),
        patch("multi_agent.research_phase1r.reporting.evaluate_gates"),
        patch("multi_agent.research_phase1r.reporting.package"),
    ):
        assert diagnostic.run(args) == 1
    server.assert_called_once()
    campaign = audit.read(root / "campaign.json")
    rows = campaign["schedule"]
    assert Counter(r["condition"] for r in rows) == {"B0": 15, "B1n": 15, "B1p": 15}
    assert len({r["trial_id"] for r in rows}) == 45
    assert len({r["trial_seed"] for r in rows}) == 45
    assert [r["trial_id"] for r in rows] == [r["trial_id"] for r in diagnostic.schedule(15, 0)]
    assert campaign["attempts"] == []  # Deliberate CPU stop happened before any worker.


def test_v3_followup_reconstructs_missing_goal_and_bad_risk_direction():
    folder = target.REPO / "results/research_phase1r/diagnostic_smoke/v3/trials/B0-0"
    if not folder.exists():
        pytest.skip("Manual v3 not available")
    result = review_trial(folder)
    assert sum(len(v["missing_goal_errors"]) for v in result["roles"].values()) == 9
    risk = result["roles"]["risk_officer"]["terminal"][0]["event"]
    assert risk["is_error"] is True and "max_tokens" in risk["error"]["message"]
    assert result["risk_updates"][0]["acting_agent"] == "compliance_analyst"
    assert result["risk_updates"][0]["role_authorized"] is False
    assert "15.0% → 20.0%" in result["risk_updates"][0]["response_text"]
    assert result["convergence"]["missing_terminal_roles"] == ["relationship_manager"]


@pytest.mark.parametrize("version", ["TARGET_V1R2", "TARGET_V1R3"])
def test_export_preserves_public_release_manifests(tmp_path, version):
    from multi_agent.research_phase1.security import digest
    from multi_agent.research_phase1r import reporting

    manifest = target.target_identity(
        controls.task(), diagnostic.configuration([0, 1, 2, 3]), version=version
    )
    path = tmp_path / "release" / (version + ".json")
    audit.write(path, manifest)
    private = dict(manifest, api_key="synthetic-secret-sentinel")
    private["target_identity"] = (
        version + ":" + digest({k: v for k, v in private.items() if k != "target_identity"})
    )
    audit.write(tmp_path / "private" / path.name, private)
    bundle = reporting.package(tmp_path, original=tmp_path / "absent")
    with tarfile.open(bundle) as archive:
        assert archive.extractfile("phase1r/release/" + path.name).read() == path.read_bytes()
        assert (
            b"synthetic-secret-sentinel"
            not in archive.extractfile("phase1r/private/" + path.name).read()
        )


@pytest.mark.parametrize(
    "mode,status", [("smoke", 0), ("smoke", 1), ("smoke", 2), ("diagnostic", 0), ("diagnostic", 1)]
)
def test_manual_wrapper_pins_limits_and_preserves_single_attempt(tmp_path, mode, status):
    scripts = tmp_path / "scripts/research_phase1"
    scripts.mkdir(parents=True)
    wrapper = scripts / "phase1r3_manual.sh"
    wrapper.write_bytes((target.REPO / "scripts/research_phase1/phase1r3_manual.sh").read_bytes())
    folder = (
        "diagnostic_smoke/v4"
        if mode == "smoke"
        else "diagnostic_calibration/r3_15_per_condition_v1"
    )
    (scripts / "phase1r.sh").write_text(
        '#!/usr/bin/env bash\ncase "$1" in\nsmoke|diagnostic)\n'
        'printf "%s\\n" "$@" > invoked_args.txt\n'
        + 'echo \'{"cleanup":{"ok":true}}\' > '
        + f"results/research_phase1r/{folder}/campaign.json\n"
        + f"exit {status}\n;;\npackage) echo package >> package_invocations.log ;;\nesac\n"
    )
    python = tmp_path / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    audit.write(
        tmp_path / "results/research_phase1r/target_v1r3/TARGET_V1R3.json",
        dict(target_identity="TARGET_V1R3:fake"),
    )
    if mode == "diagnostic":
        audit.write(
            tmp_path / "results/research_phase1r/diagnostic_smoke/v4/smoke_acceptance.json",
            dict(passed=True, target_identity="TARGET_V1R3:fake"),
        )
    env = dict(os.environ, PYTHONPATH=str(target.REPO))
    proc = subprocess.run(["bash", str(wrapper), mode], capture_output=True, text=True, env=env)
    assert proc.returncode == status, proc.stderr
    args = (tmp_path / "invoked_args.txt").read_text().splitlines()
    assert args[args.index("--repeats") + 1] == ("1" if mode == "smoke" else "15")
    assert args[args.index("--model-request-budget") + 1] == (
        "1200" if mode == "smoke" else "18000"
    )
    assert args[args.index("--parallel") + 1] == "1"
    receipt = audit.read(
        tmp_path / "results/research_phase1r" / folder / "manual_launch_receipt.json"
    )
    assert receipt["retries"] == 0 and receipt["replace_invalid_trials"] is False
    assert (tmp_path / "package_invocations.log").read_text().splitlines() == ["package"]
    again = subprocess.run(["bash", str(wrapper), mode], capture_output=True, text=True, env=env)
    assert again.returncode == 2 and "Refusing to replace" in again.stderr
    assert (tmp_path / "package_invocations.log").read_text().splitlines() == ["package"]


def test_manual_diagnostic_refuses_missing_smoke_without_reserving_directory(tmp_path):
    scripts = tmp_path / "scripts/research_phase1"
    scripts.mkdir(parents=True)
    wrapper = scripts / "phase1r3_manual.sh"
    wrapper.write_bytes((target.REPO / "scripts/research_phase1/phase1r3_manual.sh").read_bytes())
    audit.write(
        tmp_path / "results/research_phase1r/target_v1r3/TARGET_V1R3.json",
        dict(target_identity="TARGET_V1R3:fake"),
    )
    python = tmp_path / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    proc = subprocess.run(
        ["bash", str(wrapper), "diagnostic"],
        capture_output=True,
        text=True,
        env=dict(os.environ, PYTHONPATH=str(target.REPO)),
    )
    assert proc.returncode != 0 and "Smoke prerequisite missing" in proc.stderr
    assert not (tmp_path / "results/research_phase1r/diagnostic_calibration").exists()
