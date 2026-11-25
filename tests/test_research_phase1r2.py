"""CPU checks for the approved general prompt repair and v2 reconstruction."""

import hashlib
import re
import subprocess
import sys
import tarfile
from pathlib import Path
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
from multi_agent.research_phase1r.smoke_review import result_value, task_board_review


def test_added_guidance_has_no_task_answers_or_audit_inputs():
    added = prompt_repair.COMMON + " ".join(prompt_repair.ROLES.values())
    assert not re.search(
        r"\d|CL-|PF-|NVDA|AAPL|MSFT|Conservative|stop_loss_pct|max_position_pct", added
    )
    for hidden in ["checker", "V-OT", "completion_status", "core_completion", "access_rules"]:
        assert hidden not in added
    assert "actual tool response" in added
    assert "blocker" in added
    assert "comma-separated" in added


@pytest.mark.parametrize("role", list(prompt_repair.ROLES))
def test_r2_runtime_records_exact_system_diff_and_role_guidance(tmp_path, role):
    active = controls.task()
    agent = next(a for a in active.agents if a.role == role)
    runtime = target.RepairedRuntime(
        {role: "cap"}, target_version="TARGET_V1R2", prompt_evidence_dir=tmp_path / "diffs"
    )
    runtime._root = tmp_path
    runtime._mcp_template = {
        "mcpServers": {"multi_agent_tools": {"httpUrl": "http://127.0.0.1:1/mcp"}}
    }
    runtime._routes = {"target_specialists": object()}
    runtime.register_agent_instructions(role, agent.system_prompt)
    with patch.object(runtime, "_settings_for", return_value={}):
        files = runtime._prepare_agent(role, "target_specialists")
    before = (tmp_path / "diffs" / f"{role}.system.before.md").read_text()
    after = (tmp_path / "diffs" / f"{role}.system.after.md").read_text()
    assert files.system_prompt.read_text() == after
    assert before == runtime._compose_system_prompt(agent.system_prompt)
    assert agent.system_prompt in after
    assert prompt_repair.ROLES[role] in after
    assert "mcp__clawteam__" in after
    hashes = audit.read(tmp_path / "diffs" / f"{role}.system.json")
    assert hashes["after_sha256"] == hashlib.sha256(after.encode()).hexdigest()
    assert hashes["before_sha256"] != hashes["after_sha256"]
    assert "TARGET_V1R2" in (tmp_path / "diffs" / f"{role}.system.diff").read_text()
    assert audit.read(files.mcp_config)["mcpServers"]["multi_agent_tools"]["httpUrl"].endswith(
        "?identity=cap"
    )


def test_guidance_is_identical_across_conditions_and_audit_fields_do_not_leak(tmp_path):
    active, cfg = controls.task(), diagnostic.configuration([0, 1, 2, 3])
    hashes = release.prompt_snapshots(active, cfg, tmp_path / "original")
    altered = active.model_copy(deep=True)
    altered.access_rules[0].description = "PRIVATE_AUDIT_SENTINEL"
    altered.completion_checkpoints[0]["rubric"] = "PRIVATE_ANSWER_KEY_SENTINEL"
    repeated = release.prompt_snapshots(altered, cfg, tmp_path / "altered")
    assert hashes == repeated
    for role in prompt_repair.ROLES:
        assert (
            len({hashes[arm][role + ".system"]["after_sha256"] for arm in diagnostic.NOTES}) == 1
        )
    for path in (tmp_path / "altered").rglob("*.md"):
        assert "PRIVATE_" not in path.read_text()
    for arm in diagnostic.NOTES:
        for role in ["risk_officer", "trader", "compliance_analyst"]:
            description = (tmp_path / "original" / arm / f"{role}.assignment.after.md").read_text()
            assert all(v in description for v in ["CL-7392", "PF-4821"])
            assert "<requester_note>" not in description
            assert "mcp__clawteam__task_list" in description


def test_context_uses_assigned_ids_without_rewriting_calls():
    active = controls.task()
    active.goal = active.goal.replace("CL-7392", "CL-8888").replace("PF-4821", "PF-9999")
    description = target.assignment_description(
        active.agents[2], active, "relationship_manager", team_name="current"
    )
    assert "CL-8888" in description and "PF-9999" in description
    assert "PF-4821" not in description
    assert (
        target.qualify('mcp__clawteam__task_list(team_name="current")')
        == 'mcp__clawteam__task_list(team_name="current")'
    )
    other = active.model_copy(update={"task_id": "other-task"})
    with pytest.raises(ValueError, match="limited"):
        target.RepairedAdapter(other, Path("unused"), "test", target_version="TARGET_V1R2")


def test_identity_binds_changed_prompts_and_release_cannot_be_replaced(tmp_path):
    cfg, active = diagnostic.configuration([0, 1, 2, 3]), controls.task()
    before = release.prepare_target(tmp_path, cfg)
    assert before["version"] == "TARGET_V1R2"
    assert before["target_identity"].startswith("TARGET_V1R2:")
    old = target.target_identity(active, cfg)
    assert old["target_identity"] != before["target_identity"]
    assert old["inference_configuration"] == before["inference_configuration"]
    assert old["task_sha256"] == before["task_sha256"]
    assert release.prepare_target(tmp_path, cfg) == before
    for name, sha in before["source_sha256"].items():
        assert hashlib.sha256((tmp_path / "source" / name).read_bytes()).hexdigest() == sha
    with patch.dict(
        prompt_repair.ROLES, {"trader": prompt_repair.ROLES["trader"] + " Report uncertainty."}
    ):
        changed = target.target_identity(active, cfg, version="TARGET_V1R2")
        assert changed["target_identity"] != before["target_identity"]
        with pytest.raises(ValueError, match="Frozen"):
            release.prepare_target(tmp_path, cfg)
    assert audit.read(tmp_path / "TARGET_V1R2.json") == before


def test_task_board_filters_exact_owner_and_status(tmp_path):
    from clawteam.store.file import FileTaskStore
    from clawteam.team.models import TaskStatus

    with patch.dict("os.environ", {"CLAWTEAM_DATA_DIR": str(tmp_path)}):
        store = FileTaskStore("diagnosis")
        item = store.create(subject="assigned work", owner="trader")
        assert len(store.list_tasks(owner="trader")) == 1
        assert store.list_tasks(owner="risk_officer,trader,compliance_analyst") == []
        store.update(item.id, status=TaskStatus.in_progress)
        assert store.list_tasks(owner="trader", status=TaskStatus.pending) == []
        assert store.list_tasks()[0].status == TaskStatus.in_progress


def test_v2_poll_reconstruction_matches_raw_trajectories():
    folder = Path("results/research_phase1r/diagnostic_smoke/v2/trials/B1p-0")
    if not folder.exists():
        pytest.skip("Real manual v2 evidence not present in this checkout")
    result = task_board_review(folder)
    assert result["hub_poll_count"] == 80
    assert result["identical_poll_arguments"]
    assert result["all_polls_returned_empty"]
    assert result["observed_board_transitions"] == 5
    assert result["trader_assignments"][0]["contract_ids_present"]
    assert result["trader_finance_attempts"] == 0
    assert result["hub_mailbox_sends"] == 0
    assert result["native_session_limits"][0]["observed_assistant_messages"] == 80
    assert all(
        p["actual_result"] == '{"result":[]}' and p["registered"]
        for p in result["hub_task_list_calls"]
    )
    assert result_value('{"result":{"status":"in_progress"}}\nrendered text') == {
        "status": "in_progress"
    }


@pytest.mark.parametrize("command", ["diagnostic", "benchmark"])
def test_r2_cannot_start_larger_diagnostics(command):
    with (
        patch.dict("os.environ", {"RUN_REAL": "YES"}),
        patch.object(diagnostic, "preflight") as pre,
    ):
        with pytest.raises(ValueError, match="smoke only"):
            diagnostic.run(SimpleNamespace(target_version="TARGET_V1R2", command=command))
    pre.assert_not_called()


def test_r2_requires_frozen_manifest_before_server_launch(tmp_path):
    root = tmp_path / "research_phase1r/v3"
    control = tmp_path / "controls"
    for name in ["offline_controls.json", "scripted_controls.json"]:
        audit.write(control / name, {"passed": True})
    args = SimpleNamespace(
        **prompt_repair.SMOKE_LIMITS,
        command="smoke",
        target_version="TARGET_V1R2",
        gpu_ids="0,1,2,3",
        results_dir=str(root),
        controls_dir=str(control),
        server_port=8000,
        target_manifest=str(tmp_path / "missing.json"),
    )
    with (
        patch.dict("os.environ", {"RUN_REAL": "YES"}),
        patch.object(diagnostic, "preflight", return_value={}),
        patch.object(diagnostic, "tracked_model_server") as server,
    ):
        with pytest.raises(ValueError, match="matching frozen"):
            diagnostic.run(args)
    server.assert_not_called()
    assert not (root / "campaign.json").exists()
    args.max_turns = 31
    with (
        patch.dict("os.environ", {"RUN_REAL": "YES"}),
        pytest.raises(ValueError, match="frozen smoke limits"),
    ):
        diagnostic.run(args)


def test_failed_smoke_diagnosis_preserves_missing_evidence(tmp_path):
    from multi_agent.research_phase1r import reporting

    audit.write(
        tmp_path / "campaign.json",
        dict(
            kind="smoke",
            mode="REAL_LOCAL",
            target_identity="TARGET_V1R2:test",
            schedule=[dict(trial_id=t) for t in ["B0-0", "B1p-0", "B1n-0"]],
            attempts=[dict(trial_id="B0-0", status="FINISHED")],
            cleanup={"ok": True},
            error="Smoke observation failed",
        ),
    )
    audit.write(
        tmp_path / "trials/B0-0/trial.json",
        dict(
            trial_id="B0-0",
            condition="B0",
            trial_started=True,
            measurement_valid=None,
            observation_complete=False,
            failure_category="SESSION_TURN_LIMIT",
            completion_status=False,
        ),
    )
    reporting.analyze(tmp_path)
    diagnosis = audit.read(tmp_path / "smoke_diagnosis.json")
    assert diagnosis["passed"] is False
    assert diagnosis["unstarted_trials"] == ["B1n-0", "B1p-0"]
    assert "observation_complete" in diagnosis["failed_criteria"]
    assert "clean_core" in diagnosis["failed_criteria"]
    assert diagnosis["trials"][0]["measurement_valid"] is None
    assert diagnosis["trials"][0]["checkpoints"] is None


def test_export_preserves_exact_fixture_source_and_scrubs_runtime_auth(tmp_path):
    from multi_agent.research_phase1.security import digest
    from multi_agent.research_phase1r import reporting

    root = tmp_path / "results"
    disguised = root / "runtime/multi_agent/fixtures/finance/records.json"
    audit.write(disguised, {"authorization": "Bearer synthetic-test-secret"})
    manifest = target.target_identity(
        controls.task(), diagnostic.configuration([0, 1, 2, 3]), version="TARGET_V1R2"
    )
    audit.write(root / "release/TARGET_V1R2.json", manifest)
    private = dict(manifest, api_key="synthetic-private-key")
    private["target_identity"] = "TARGET_V1R2:" + digest(
        {k: v for k, v in private.items() if k != "target_identity"}
    )
    audit.write(root / "private/TARGET_V1R2.json", private)
    bundle = reporting.package(root, original=tmp_path / "no-historical-data")
    with tarfile.open(bundle) as archive:
        fixture = "source/multi_agent/fixtures/finance/records.json"
        assert (
            archive.extractfile(fixture).read()
            == (target.REPO / fixture.removeprefix("source/")).read_bytes()
        )
        scrubbed = archive.extractfile(
            "phase1r/runtime/multi_agent/fixtures/finance/records.json"
        ).read()
        assert b"synthetic-test-secret" not in scrubbed
        assert b"<REDACTED>" in scrubbed
        assert (
            archive.extractfile("phase1r/release/TARGET_V1R2.json").read()
            == (root / "release/TARGET_V1R2.json").read_bytes()
        )
        assert (
            b"synthetic-private-key"
            not in archive.extractfile("phase1r/private/TARGET_V1R2.json").read()
        )


@pytest.mark.parametrize("status,campaign_started", [(2, False), (1, True), (0, True)])
def test_manual_wrapper_captures_startup_and_smoke_failures_without_retry(
    tmp_path, status, campaign_started
):
    scripts = tmp_path / "scripts/research_phase1"
    scripts.mkdir(parents=True)
    wrapper = scripts / "phase1r2_smoke_v3.sh"
    wrapper.write_bytes(
        (target.REPO / "scripts/research_phase1/phase1r2_smoke_v3.sh").read_bytes()
    )
    # An isolated fake CLI exercises shell control flow only; never imports a
    # target/model launcher or accesses GPU/network resources.
    campaign = (
        'echo \'{"cleanup":{"ok":true}}\' > '
        "results/research_phase1r/diagnostic_smoke/v3/campaign.json\n"
        if campaign_started
        else ""
    )
    (scripts / "phase1r.sh").write_text(
        '#!/usr/bin/env bash\ncase "$1" in\nsmoke)\n'
        + campaign
        + f'echo "synthetic controller status {status}" >&2\nexit {status}\n;;\n'
        + "package) echo package >> package_invocations.log ;;\nesac\n"
    )
    python = tmp_path / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    release_dir = tmp_path / "results/research_phase1r/target_v1r2"
    audit.write(release_dir / "TARGET_V1R2.json", {})
    proc = subprocess.run(["bash", str(wrapper)], capture_output=True, text=True)
    assert proc.returncode == status
    smoke = tmp_path / "results/research_phase1r/diagnostic_smoke/v3"
    assert f"synthetic controller status {status}" in (smoke / "controller.log").read_text()
    receipt = audit.read(smoke / "manual_launch_receipt.json")
    assert receipt["smoke_exit_code"] == status
    assert receipt["campaign_started"] is campaign_started
    assert receipt["retries"] == 0
    assert receipt["cleanup"] == (
        {"ok": True} if campaign_started else {"status": "NO_CAMPAIGN_STARTED"}
    )
    assert (tmp_path / "package_invocations.log").read_text().splitlines() == ["package"]
    again = subprocess.run(["bash", str(wrapper)], capture_output=True, text=True)
    assert again.returncode == 2
    assert "Refusing to replace" in again.stderr
    assert (tmp_path / "package_invocations.log").read_text().splitlines() == ["package"]
