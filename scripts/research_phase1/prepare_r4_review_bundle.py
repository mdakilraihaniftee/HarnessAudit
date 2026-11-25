#!/usr/bin/env python3
"""CPU-only export of the BACH TARGET_V1R4 reviewer bundle. Never starts inference.

Usage:
  .venv/bin/python scripts/research_phase1/prepare_r4_review_bundle.py [--record-tests]

--record-tests first runs the CPU validation commands and records their results in
results/research_phase1r/target_v1r4/tests/. The bundle is then built in a fresh
results/chatgpt_handoffs/r4-<timestamp>/ folder, extracted to a temporary directory
and checked with the bundled stdlib validator. Export fails if validation fails.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

REPO = Path(__file__).resolve().parents[2]
RESULTS = REPO / "results/research_phase1r"
RELEASE = RESULTS / "target_v1r4"
SMOKES = RESULTS / "diagnostic_smoke"
STUDY = RESULTS / "diagnostic_calibration/r4_5_per_condition_v1"
STUDY_PREFIX = "evidence/diagnostic_calibration/r4_5_per_condition_v1"
EXCLUDED_PARTS = {"source", "workspaces", "qwen_home", ".control", "__pycache__"}
EXCLUDED_NAMES = {
    "mcp.json",  # per-agent capability tokens
    "private_server_spec.json",
    ".experiment.lock",
    "reviewer_bundle.tar.gz",
}
CPU_ENV = dict(
    RUN_REAL="NO",
    HA_INFERENCE_MODE="local_only",
    HA_ALLOW_MODEL_DOWNLOAD="false",
    HF_HUB_OFFLINE="1",
    TRANSFORMERS_OFFLINE="1",
)
PY = str(REPO / ".venv/bin/python")
CPU_COMMANDS = [
    ("Full repository tests", [PY, "-m", "pytest", "-q", "-p", "no:cacheprovider"]),
    (
        "Phase 1 tests",
        [
            PY,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "tests/test_research_phase1.py",
            "tests/test_research_launcher.py",
        ],
    ),
    (
        "Phase 1R tests (R1-R3 + validator)",
        [
            PY,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "tests/test_research_phase1r.py",
            "tests/test_research_phase1r2.py",
            "tests/test_research_phase1r3.py",
            "tests/test_research_phase1r_validator.py",
        ],
    ),
    (
        "V1R4 tests",
        [PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/test_research_phase1r4.py"],
    ),
    (
        "Ruff lint (research code)",
        [
            PY,
            "-m",
            "ruff",
            "check",
            "multi_agent/research_phase1r",
            "multi_agent/research_phase1",
            "scripts/research_phase1",
            "tests",
        ],
    ),
    (
        "Python compilation (warnings as errors)",
        [
            PY,
            "-W",
            "error",
            "-m",
            "compileall",
            "-q",
            "multi_agent/research_phase1r",
            "multi_agent/research_phase1",
            "scripts/research_phase1",
        ],
    ),
    (
        "Bash syntax",
        ["bash", "-c", 'for f in scripts/research_phase1/*.sh; do bash -n "$f" || exit 1; done'],
    ),
    ("git diff --check", ["git", "diff", "--check"]),
    (
        "Trailing whitespace in untracked research files",
        [
            "bash",
            "-c",
            "! git ls-files --others --exclude-standard -z multi_agent scripts tests docs "
            "| xargs -0 grep -nI '[[:blank:]]$'",
        ],
    ),
    ("Historical preservation", [PY, "-m", "multi_agent.research_phase1r", "verify-preservation"]),
    (
        "Tool contract schema comparison",
        [
            PY,
            "-m",
            "multi_agent.research_phase1r",
            "test-tool-contract",
            "--results-dir",
            str(RELEASE / "schema_evidence"),
        ],
    ),
]


def read(path, default=None):
    path = Path(path)
    return json.loads(path.read_text()) if path.is_file() else default


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def record_tests():
    out = RELEASE / "tests"
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for i, (name, command) in enumerate(CPU_COMMANDS):
        log = out / f"{i:02d}_{name.lower().replace(' ', '_').replace('/', '_')[:40]}.log"
        started = time.time()
        proc = subprocess.run(
            command, cwd=REPO, env=dict(os.environ, **CPU_ENV), capture_output=True, text=True
        )
        log.write_text(f"$ {' '.join(command)}\n\n{proc.stdout}\n{proc.stderr}")
        tail = [line for line in (proc.stdout + proc.stderr).splitlines() if line.strip()]
        rows.append(
            dict(
                test=name,
                command=" ".join(c.replace(str(REPO) + "/", "") for c in command),
                status="PASS" if proc.returncode == 0 else "FAIL",
                exit_code=proc.returncode,
                seconds=round(time.time() - started, 2),
                summary=tail[-1][:300] if tail else "",
                evidence=str(log.relative_to(REPO)),
            )
        )
        print(f"{rows[-1]['status']:5} {name}: {rows[-1]['summary']}", flush=True)
    (out / "results.json").write_text(
        json.dumps(dict(recorded_at=time.time(), cpu_only=True, tests=rows), indent=2) + "\n"
    )
    return rows


def cross_version_ledgers():
    sys.path.insert(0, str(REPO))
    from multi_agent.research_phase1r.coordination_audit import (
        all_calls,
        ledger_summary,
        progress_diagnostic,
        role_ledger,
    )

    rows = []
    for trial in sorted(SMOKES.glob("v*/trials/*")):
        calls = all_calls(trial)
        ledger = role_ledger(trial, "relationship_manager", calls)
        summary = ledger_summary(trial, "relationship_manager", ledger, calls)
        first = ledger[0] if ledger else {}
        rows.append(
            dict(
                smoke=trial.parts[-3],
                trial=trial.name,
                target_identity=(read(trial / "trial.json", {}) or {}).get("target_identity"),
                first_call=dict(
                    tool=first.get("tool_name"),
                    arguments={
                        k: v
                        for k, v in (first.get("input_arguments") or {}).items()
                        if k != "team_name"
                    },
                ),
                **{
                    k: summary[k]
                    for k in [
                        "tool_call_count",
                        "rejected_tool_invocations",
                        "empty_responses",
                        "comma_owner_filter_calls",
                        "valid_unfiltered_task_list_calls",
                        "mailbox_send_count",
                        "mailbox_receive_count",
                        "invalid_enum_values",
                        "stop_reason",
                        "final_response_observed",
                    ]
                },
                progress_labels={
                    r: v["descriptive_label"]
                    for r, v in progress_diagnostic(trial, calls)["roles"].items()
                },
            )
        )
    data = dict(
        method="read-only coordinator ledger over every historical smoke trial",
        trials=rows,
    )
    (RELEASE / "cross_version_coordinator_ledgers.json").write_text(
        json.dumps(data, indent=2) + "\n"
    )
    return data


def source_diff():
    r3 = RESULTS / "target_v1r3/source"
    r4 = read(RELEASE / "TARGET_V1R4.json")
    names = sorted(
        set(r4["source_sha256"]) | {str(p.relative_to(r3)) for p in r3.rglob("*") if p.is_file()}
    )
    chunks, listing = [], []
    for name in names:
        old, new = r3 / name, REPO / name
        a = old.read_text().splitlines(keepends=True) if old.is_file() else []
        b = new.read_text().splitlines(keepends=True) if new.is_file() else []
        if a == b:
            continue
        status = "added" if not a else "removed" if not b else "modified"
        listing.append(dict(path=name, status=status))
        chunks.extend(
            difflib.unified_diff(
                a, b, fromfile=f"TARGET_V1R3/{name}", tofile=f"TARGET_V1R4/{name}"
            )
        )
    return "".join(chunks), listing


def copy_tree(src, dest, stage, files):
    for path in sorted(src.rglob("*")):
        rel = path.relative_to(src)
        if (
            not path.is_file()
            or path.is_symlink()
            or path.name in EXCLUDED_NAMES
            or any(part in EXCLUDED_PARTS for part in rel.parts)
        ):
            continue
        target = stage / dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        files.append(str(Path(dest) / rel))


def summary(attribution, ledger, tests):
    v5 = SMOKES / "v5"
    acceptance = read(v5 / "smoke_acceptance.json")
    r4 = read(RELEASE / "TARGET_V1R4.json")
    completion = read(
        SMOKES / "v4/trials/B0-0/instrumentation/effects/deterministic_completion.json"
    )
    return dict(
        generated_at=datetime.now(UTC).isoformat(),
        decision="A_WITH_B_RISK: genuine model-visible schema defect repaired at Level 1; "
        "whether Qwen3-8B now coordinates is untested until the manual v5 smoke",
        target_v1r4=dict(
            identity=r4["target_identity"],
            changed_schema_fields=r4["coordination_tool_contract"]["changed_fields"],
            prompts_changed=False,
            inference_launched_by_coding_agent=False,
        ),
        v4_coordinator=dict(
            calls=ledger["tool_call_count"],
            rejected=ledger["rejected_tool_invocations"],
            empty=ledger["empty_responses"],
            comma_owner_calls=ledger["comma_owner_filter_calls"],
            valid_unfiltered_task_list_calls=ledger["valid_unfiltered_task_list_calls"],
            mailbox_sends=ledger["mailbox_send_count"],
            invalid_status_values=ledger["distinct_invalid_status_values"],
            stop_reason=ledger["stop_reason"],
        ),
        v4_effects={
            k: attribution[k]
            for k in [
                "state_changing_calls",
                "attempted_role_violations",
                "verified_unauthorized_effects",
                "failed_unauthorized_attempts",
                "valid_authorized_effects",
                "effects_with_unknown_attribution",
                "verified_committed_effects",
            ]
        },
        v4_completion={c["name"]: c["status"] for c in completion["checkpoints"]},
        r4_benign_study=study_status(),
        v5_smoke=dict(
            status="RUN" if (v5 / "campaign.json").is_file() else "NOT_RUN",
            passed=acceptance.get("passed") if acceptance else None,
            criteria=acceptance.get("criteria") if acceptance else None,
            target_identity=acceptance.get("target_identity") if acceptance else None,
        ),
        not_executed=[
            dict(item=i, label="NOT EXECUTED: " + why)
            for i, why in [
                ("v4 B1n-0 and B1p-0", "smoke stopped fail-fast after B0-0; not model failures"),
                ("45-trial (15 per B0/B1n/B1p) benign diagnostic", "smoke gate not passed"),
                ("concurrency-2/4 benchmarks", "not authorized"),
                ("B2/B3 adversarial conditions, Phase 2, Bayesian BACH", "not authorized"),
            ]
        ]
        + (
            []
            if (v5 / "campaign.json").is_file()
            else [dict(item="v5 TARGET_V1R4 smoke", label="NOT EXECUTED: awaiting manual run")]
        )
        + (
            []
            if (STUDY / "campaign.json").is_file()
            else [
                dict(
                    item="TARGET_V1R4 benign study, 5 per B0/B1n/B1p",
                    label="NOT EXECUTED: awaiting manual run",
                )
            ]
        ),
        cpu_tests_all_passed=all(r["status"] == "PASS" for r in tests) if tests else False,
    )


def study_status():
    if not (STUDY / "campaign.json").is_file():
        return dict(status="NOT_RUN", started=None)
    data = read(STUDY / "r4_study_summary.json")
    if data is None:  # derive it if the run ended before its summary step
        sys.path.insert(0, str(REPO))
        from multi_agent.research_phase1r.r4_release import study_summary

        data = study_summary(STUDY)
    return dict(
        status="RUN",
        **{
            k: data[k]
            for k in [
                "target_identity",
                "scheduled",
                "started",
                "not_started",
                "campaign_error",
                "cleanup_ok",
                "measurement_valid",
                "observation_complete",
                "deterministic_core_completion",
                "coordinator",
                "effects",
                "by_condition",
                "interpretation",
            ]
        },
    )


def main(argv):
    if "--record-tests" in argv:
        record_tests()
    if not (RELEASE / "TARGET_V1R4.json").is_file():
        raise SystemExit("Missing frozen TARGET_V1R4 release")
    pres = subprocess.run(
        [PY, "-m", "multi_agent.research_phase1r", "verify-preservation"],
        cwd=REPO,
        env=dict(os.environ, **CPU_ENV),
        capture_output=True,
        text=True,
    )
    if pres.returncode != 0:
        raise SystemExit("Historical evidence changed; export refused:\n" + pres.stdout)
    cross_version_ledgers()
    audit = RELEASE / "v4_audit"
    attribution = read(audit / "effect_attribution.json")
    ledger = read(audit / "coordinator_call_ledger.json")["summary"]
    tests = (read(RELEASE / "tests/results.json") or {}).get("tests", [])

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    parent = REPO / "results/chatgpt_handoffs"
    parent.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix=f"r4-{stamp}-", dir=parent))
    with tempfile.TemporaryDirectory(prefix="bach-r4-stage-") as tmp:
        stage = Path(tmp)
        files = []
        for doc in sorted((REPO / "docs/research").iterdir()):
            if doc.is_file():
                (stage / "docs/research").mkdir(parents=True, exist_ok=True)
                shutil.copy2(doc, stage / "docs/research" / doc.name)
                files.append(f"docs/research/{doc.name}")
        diff, listing = source_diff()
        (stage / "source_diffs").mkdir()
        (stage / "source_diffs/r3_to_r4.diff").write_text(diff)
        (stage / "source_diffs/changed_files.json").write_text(json.dumps(listing, indent=2))
        files += ["source_diffs/r3_to_r4.diff", "source_diffs/changed_files.json"]
        copy_tree(RELEASE, "evidence/target_v1r4", stage, files)
        for version in ["target_v1r3", "target_v1r2"]:
            for name in [f"{version.upper()}.json", "release_status.json", "RELEASE_STATUS.json"]:
                src = RESULTS / version / name
                if src.is_file():
                    (stage / "evidence" / version).mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, stage / "evidence" / version / name)
                    files.append(f"evidence/{version}/{name}")
        if STUDY.is_dir():
            copy_tree(STUDY, STUDY_PREFIX, stage, files)
        for smoke in ["v4", "v5"]:
            if (SMOKES / smoke).is_dir():
                copy_tree(SMOKES / smoke, f"evidence/diagnostic_smoke/{smoke}", stage, files)
        for smoke in ["v1", "v2", "v3"]:
            src = SMOKES / smoke
            for rel in ["smoke_acceptance.json", "smoke_diagnosis.json", "campaign.json"]:
                if (src / rel).is_file():
                    dest = stage / "evidence/diagnostic_smoke" / smoke / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src / rel, dest)
                    files.append(f"evidence/diagnostic_smoke/{smoke}/{rel}")
            for trial in sorted(src.glob("trials/*")):
                for rel in ["trial.json", "instrumentation/effects/deterministic_completion.json"]:
                    if (trial / rel).is_file():
                        dest = (
                            stage
                            / "evidence/diagnostic_smoke"
                            / smoke
                            / "trials"
                            / trial.name
                            / rel
                        )
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(trial / rel, dest)
                        files.append(str(dest.relative_to(stage)))
        handoff = stage / "HANDOFF"
        handoff.mkdir()
        result = summary(attribution, ledger, tests)
        (handoff / "RESULTS_SUMMARY.json").write_text(json.dumps(result, indent=2) + "\n")
        (handoff / "README.md").write_text(
            "# BACH TARGET_V1R4 reviewer bundle\n\n"
            "Start with HANDOFF/RESULTS_SUMMARY.json, then "
            "docs/research/BACH_R3_INDEPENDENT_ROOT_CAUSE.md and BACH_NEXT_DECISION.md.\n\n"
            "Validate before reading: `python validate_bundle.py .` (stdlib only).\n\n"
            "- `evidence/diagnostic_smoke/v4/` raw v4 traces, effect ledger and SQLite "
            "snapshots (synthetic FinanceBank; per-agent capability files excluded).\n"
            "- `evidence/target_v1r4/` frozen release, schema evidence, v4 audit, tests.\n"
            "- `source_diffs/r3_to_r4.diff` every source change relative to frozen R3.\n"
            f"- v5 smoke status: {result['v5_smoke']['status']}.\n"
        )
        (handoff / "REPRODUCE.md").write_text(
            "# Reproduce (from /home/ai5377/HarnessAudit)\n\n"
            "CPU only:\n\n```bash\n"
            "bash scripts/research_phase1/phase1r4_manual.sh preflight\n"
            "bash scripts/research_phase1/phase1r4_manual.sh test-tool-contract\n"
            "bash scripts/research_phase1/phase1r4_manual.sh audit-v4\n"
            "bash scripts/research_phase1/phase1r4_manual.sh package\n```\n\n"
            "Manual GPU smoke (writes results/research_phase1r/diagnostic_smoke/v5):\n\n"
            "```bash\nbash scripts/research_phase1/phase1r4_manual.sh gpu-preflight && "
            "bash scripts/research_phase1/phase1r4_manual.sh smoke\n```\n"
        )
        (handoff / "NEXT_ACTION_REQUEST.txt").write_text(
            "Review this BACH TARGET_V1R4 bundle. Validate it with validate_bundle.py first. "
            "Decide only the next single step described in docs/research/BACH_NEXT_DECISION.md. "
            "Do not treat unrun items as results.\n"
        )
        files += [
            f"HANDOFF/{n}"
            for n in [
                "RESULTS_SUMMARY.json",
                "README.md",
                "REPRODUCE.md",
                "NEXT_ACTION_REQUEST.txt",
            ]
        ]
        shutil.copy2(
            Path(__file__).with_name("validate_r4_review_bundle.py"), stage / "validate_bundle.py"
        )
        files.append("validate_bundle.py")
        manifest = dict(
            created_at=result["generated_at"],
            target_v1r4=result["target_v1r4"]["identity"],
            excluded=dict(parts=sorted(EXCLUDED_PARTS), names=sorted(EXCLUDED_NAMES)),
            files={f: sha(stage / f) for f in sorted(set(files))},
        )
        (stage / "MANIFEST.json").write_text(json.dumps(manifest, indent=1) + "\n")
        archive = output / "CHATGPT_R4_REVIEW.zip"
        with ZipFile(archive, "w", ZIP_DEFLATED) as zf:
            for path in sorted(stage.rglob("*")):
                if path.is_file():
                    zf.write(path, path.relative_to(stage))
    check = subprocess.run(
        [
            sys.executable,
            "-I",
            str(Path(__file__).with_name("validate_r4_review_bundle.py")),
            str(archive),
        ],
        capture_output=True,
        text=True,
    )
    (output / "validation_receipt.json").write_text(check.stdout)
    shutil.copy2(output / "validation_receipt.json", output / "bundle_validation.json")
    (output / "NEXT_ACTION_REQUEST.txt").write_text(
        (REPO / "docs/research/BACH_NEXT_DECISION.md").read_text()
        if (REPO / "docs/research/BACH_NEXT_DECISION.md").is_file()
        else ""
    )
    print(
        json.dumps(
            dict(
                archive=str(archive),
                validation_passed=check.returncode == 0,
                files=len(manifest["files"]),
            ),
            indent=2,
        )
    )
    if check.returncode != 0:
        print(check.stdout, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
