"""TARGET_V1R4 CPU-only release freezing, preservation verification and preflight."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from .audit import read, write
from .target import REPO
from .tool_contract import VERSION

RELEASE_DIR = REPO / "results/research_phase1r/target_v1r4"
SMOKE_DIR = REPO / "results/research_phase1r/diagnostic_smoke/v5"
STUDY_DIR = REPO / "results/research_phase1r/diagnostic_calibration/r4_5_per_condition_v1"
V4_TRIAL = REPO / "results/research_phase1r/diagnostic_smoke/v4/trials/B0-0"
HISTORICAL_RELEASES = {
    "TARGET_V1R2": REPO / "results/research_phase1r/target_v1r2/TARGET_V1R2.json",
    "TARGET_V1R3": REPO / "results/research_phase1r/target_v1r3/TARGET_V1R3.json",
}
R3_IDENTITY = "TARGET_V1R3:d02cf01923192cf2ad42195b332122c8ff9510e01e63f657d5ea5d23d2607967"


def verify_preservation(receipt=None):
    """Every file hashed before the R4 edits must still exist byte-for-byte."""
    receipt = Path(receipt or RELEASE_DIR / "preservation_before.json")
    before = read(receipt, {})
    if not before.get("files"):
        raise ValueError(f"Missing pre-edit preservation receipt: {receipt}")
    changed, missing = [], []
    for name, sha in before["files"].items():
        path = REPO / name
        if not path.is_file():
            missing.append(name)
        elif hashlib.sha256(path.read_bytes()).hexdigest() != sha:
            changed.append(name)
    return dict(
        receipt=str(receipt.relative_to(REPO)),
        protected_files=len(before["files"]),
        missing=missing,
        changed=changed,
        preserved=not missing and not changed,
        checked_at=time.time(),
    )


def historical_identities():
    out = {}
    for version, path in HISTORICAL_RELEASES.items():
        data = read(path, {})
        out[version] = dict(path=str(path.relative_to(REPO)), identity=data.get("target_identity"))
    out["TARGET_V1R3"]["matches_recorded_v4_identity"] = (
        out["TARGET_V1R3"]["identity"] == R3_IDENTITY
    )
    return out


def freeze(config):
    """Freeze TARGET_V1R4 once; refuses to replace a different existing release."""
    import os

    from .release import prepare_target
    from .tool_contract import schema_comparison

    # The inference configuration records which environment overrides were applied,
    # so freezing must use exactly the launcher's environment (as R2/R3 releases did).
    if (
        os.environ.get("HA_INFERENCE_MODE") != "local_only"
        or os.environ.get("HA_ALLOW_MODEL_DOWNLOAD") != "false"
    ):
        raise ValueError(
            "Freeze through `phase1r4_manual.sh freeze` so the identity matches launch"
        )
    manifest = prepare_target(RELEASE_DIR, config, version=VERSION)
    comparison = schema_comparison()
    if (
        comparison["model_visible_schema_sha256"]
        != (manifest["coordination_tool_contract"]["model_visible_schema_sha256"])
    ):
        raise ValueError("Schema changed during release preparation")
    write(RELEASE_DIR / "schema_evidence/tool_schema_comparison.json", comparison)
    status = dict(
        version=VERSION,
        target_identity=manifest["target_identity"],
        frozen_at=time.time(),
        preservation=verify_preservation(),
        historical_identities=historical_identities(),
        inference_launched=False,
        approved_next_step="manual v5 smoke only",
    )
    write(RELEASE_DIR / "release_status.json", status)
    if not status["preservation"]["preserved"]:
        raise ValueError("Historical evidence changed; see release_status.json")
    return manifest


def preflight(config):
    """CPU-only readiness check before a manual v5 smoke. Never starts a model or GPU."""
    from multi_agent.frameworks.native.qwen_code.runtime import resolve_qwen_bin
    from multi_agent.local_inference.model_registry import local_snapshot

    from .controls import task
    from .target import target_identity

    frozen = read(RELEASE_DIR / f"{VERSION}.json", {})
    current = target_identity(task(), config, version=VERSION)
    controls = REPO / "results/research_phase1r/instrumentation_controls"
    try:
        qwen = resolve_qwen_bin(config.harness.qwen_code.bin)
    except (OSError, RuntimeError) as exc:
        qwen = f"UNAVAILABLE: {exc}"
    checks = dict(
        release_frozen=bool(frozen),
        current_identity_matches_frozen=frozen.get("target_identity")
        == current["target_identity"],
        historical_evidence_preserved=verify_preservation()["preserved"],
        r3_identity_unchanged=historical_identities()["TARGET_V1R3"][
            "matches_recorded_v4_identity"
        ],
        offline_controls_passed=read(controls / "offline_controls.json", {}).get("passed") is True,
        scripted_controls_passed=read(controls / "scripted_controls.json", {}).get("passed")
        is True,
        v5_output_absent=not SMOKE_DIR.exists(),
        study_output_absent=not STUDY_DIR.exists(),
        pinned_checkpoint_cached=local_snapshot(
            config.inference.model, config.inference.model_revision
        )
        is not None,
        qwen_cli_available=not str(qwen).startswith("UNAVAILABLE"),
        temperature_0_7=config.inference.temperature == 0.7,
        thinking_off=config.inference.reasoning_mode == "off",
        context_32768=config.inference.context_length == 32768,
        native_turns_80=config.harness.qwen_code.max_session_turns == 80,
        paid_fallback_disabled=config.inference.allow_paid_fallback is False,
        downloads_disabled=config.inference.allow_model_download is False,
    )
    result = dict(
        version=VERSION,
        frozen_identity=frozen.get("target_identity"),
        current_identity=current["target_identity"],
        qwen_cli=str(qwen),
        checks=checks,
        ready_for_manual_smoke=all(checks.values()),
        gpu_checked=False,
        note="CPU-only. GPU eligibility is checked separately by gpu-preflight and again "
        "by the controller immediately before launch.",
    )
    write(RELEASE_DIR / "preflight.json", result)
    return result


def json_dump(data):
    return json.dumps(data, indent=2, default=str)


def study_summary(root):
    """Descriptive per-trial and aggregate readout of a TARGET_V1R4 campaign.

    Denominators are explicit; unknown or missing evidence is reported as unknown,
    never as zero or as a pass. Nothing is pooled with other target versions.
    """
    root = Path(root)
    campaign = read(root / "campaign.json", {})
    attempts = {a["trial_id"]: a for a in campaign.get("attempts", [])}
    rows = []
    for item in campaign.get("schedule", []):
        tid = item["trial_id"]
        folder, derived = root / "trials" / tid, root / "derived" / tid
        attempt = attempts.get(tid)
        trial = read(folder / "trial.json", {})
        ledger = read(derived / "coordinator_call_ledger.json", {})
        summary = ledger.get("summary", {})
        calls = ledger.get("calls") or []
        progress = read(derived / "coordination_progress.json", {}).get("roles", {})
        effects = read(derived / "effect_attribution.json", {})
        completion = read(folder / "instrumentation/effects/deterministic_completion.json", {})
        first = calls[0] if calls else {}
        first_owner = (first.get("input_arguments") or {}).get("owner")
        rows.append(
            dict(
                trial_id=tid,
                condition=item.get("condition"),
                started=attempt is not None and attempt.get("status") != "RESERVED",
                exit_code=(attempt or {}).get("exit_code"),
                measurement_valid=trial.get("measurement_valid"),
                observation_complete=trial.get("observation_complete"),
                failure_category=trial.get("failure_category"),
                core_completion=completion.get("core_completion") if completion else None,
                checkpoints={c["name"]: c["status"] for c in completion.get("checkpoints", [])},
                derived_available=bool(summary),
                coordinator=dict(
                    first_tool=first.get("tool_name"),
                    first_owner_filter=first_owner,
                    first_call_comma_owner=bool(first_owner and "," in first_owner)
                    if calls
                    else None,
                    calls=summary.get("tool_call_count"),
                    rejected=summary.get("rejected_tool_invocations"),
                    empty=summary.get("empty_responses"),
                    comma_owner_calls=summary.get("comma_owner_filter_calls"),
                    valid_unfiltered_task_list_calls=summary.get(
                        "valid_unfiltered_task_list_calls"
                    ),
                    mailbox_sends=summary.get("mailbox_send_count"),
                    final_response_observed=summary.get("final_response_observed"),
                    stop_reason=summary.get("stop_reason"),
                    label=progress.get("relationship_manager", {}).get("descriptive_label"),
                ),
                effects={
                    k: effects.get(k)
                    for k in [
                        "attempted_role_violations",
                        "verified_unauthorized_effects",
                        "failed_unauthorized_attempts",
                        "valid_authorized_effects",
                        "effects_with_unknown_attribution",
                        "verified_committed_effects",
                    ]
                },
            )
        )
    started = [r for r in rows if r["started"]]
    known = [r for r in started if r["derived_available"]]

    def tally(values):
        return dict(
            true=sum(v is True for v in values),
            false=sum(v is False for v in values),
            unknown=sum(v is None for v in values),
            denominator=len(values),
        )

    def total(key):
        values = [r["effects"].get(key) for r in known]
        return dict(
            sum=sum(v for v in values if isinstance(v, int)),
            trials_with_value=sum(isinstance(v, int) for v in values),
            trials_without_evidence=len(started) - sum(isinstance(v, int) for v in values),
        )

    result = dict(
        kind=campaign.get("kind"),
        target_identity=campaign.get("target_identity"),
        scheduled=len(rows),
        started=len(started),
        not_started=[r["trial_id"] for r in rows if not r["started"]],
        campaign_error=campaign.get("error"),
        cleanup_ok=(campaign.get("cleanup") or {}).get("ok"),
        measurement_valid=tally([r["measurement_valid"] for r in started]),
        observation_complete=tally([r["observation_complete"] for r in started]),
        deterministic_core_completion=tally([r["core_completion"] for r in started]),
        coordinator=dict(
            first_call_comma_owner=tally(
                [r["coordinator"]["first_call_comma_owner"] for r in started]
            ),
            final_response_observed=tally(
                [r["coordinator"]["final_response_observed"] for r in started]
            ),
            no_progress_loop=sum(r["coordinator"]["label"] == "NO_PROGRESS_LOOP" for r in known),
            session_turn_limit=sum(
                r["coordinator"]["stop_reason"] == "SESSION_TURN_LIMIT" for r in known
            ),
            used_valid_unfiltered_task_list=sum(
                (r["coordinator"]["valid_unfiltered_task_list_calls"] or 0) > 0 for r in known
            ),
            sent_mailbox_messages=sum((r["coordinator"]["mailbox_sends"] or 0) > 0 for r in known),
            derived_evidence_missing=len(started) - len(known),
        ),
        effects={
            k: total(k)
            for k in [
                "attempted_role_violations",
                "verified_unauthorized_effects",
                "failed_unauthorized_attempts",
                "valid_authorized_effects",
                "effects_with_unknown_attribution",
                "verified_committed_effects",
            ]
        },
        by_condition={
            arm: dict(
                started=sum(r["condition"] == arm for r in started),
                measurement_valid=tally(
                    [r["measurement_valid"] for r in started if r["condition"] == arm]
                ),
                deterministic_core_completion=tally(
                    [r["core_completion"] for r in started if r["condition"] == arm]
                ),
                coordinator_no_progress_loop=sum(
                    r["coordinator"]["label"] == "NO_PROGRESS_LOOP"
                    for r in known
                    if r["condition"] == arm
                ),
                verified_unauthorized_effects=sum(
                    r["effects"].get("verified_unauthorized_effects") or 0
                    for r in known
                    if r["condition"] == arm
                ),
            )
            for arm in sorted({r["condition"] for r in rows})
        },
        interpretation=(
            "Descriptive engineering counts for one target version (n = 5 per condition); "
            "condition differences are not estimable effects at this size; not an attack "
            "effect, not a Phase 2 result, and not pooled with earlier versions. Unknown "
            "values are not zeros."
        ),
        trials=rows,
    )
    write(root / "r4_study_summary.json", result)
    return result
