"""CPU-only export of prepared/executed R3 status; never starts inference."""

import hashlib
import json
import os
import subprocess
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def read(path, default=None):
    return json.loads(path.read_text()) if path.is_file() else default


def main():
    repo = Path(__file__).resolve().parents[2]
    results = repo / "results/research_phase1r"
    release = results / "target_v1r3"
    manifest = read(release / "TARGET_V1R3.json")
    if not manifest:
        raise SystemExit("Missing prepared R3 manifest")
    protected = read(release / "preservation_before.json")

    def preserved():
        changed = [
            name
            for name, digest in protected.items()
            if not (repo / name).is_file()
            or hashlib.sha256((repo / name).read_bytes()).hexdigest() != digest
        ]
        if changed:
            raise RuntimeError(f"Historical evidence changed: {changed}")

    preserved()
    parent = repo / "results/chatgpt_handoffs"
    parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output = Path(tempfile.mkdtemp(prefix=f"r3-{stamp}-", dir=parent))
    print(f"CPU-only export; logs and output: {output}", flush=True)
    env = dict(
        os.environ,
        RUN_REAL="NO",
        HA_INFERENCE_MODE="local_only",
        HA_ALLOW_MODEL_DOWNLOAD="false",
        HF_HUB_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1",
    )
    python = repo / ".venv/bin/python"

    def run(arguments, filename):
        with (output / filename).open("w") as log:
            subprocess.run(
                [str(python), *map(str, arguments)],
                cwd=repo,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
            )

    try:
        run(
            ["-m", "multi_agent.research_phase1r", "package", "--results-dir", results],
            "packaging.log",
        )
        with tempfile.TemporaryDirectory(prefix="bach-r3-review-") as tmp:
            stage = Path(tmp)
            with tarfile.open(results / "reviewer_bundle.tar.gz") as archive:
                archive.extractall(stage, filter="data")
            run(
                [repo / "scripts/research_phase1/verify_phase1r_release_export.py", stage],
                "independent_validation.log",
            )
            preserved()
            smoke = read(results / "diagnostic_smoke/v4/smoke_acceptance.json")
            diagnostic = results / "diagnostic_calibration/r3_15_per_condition_v1"
            campaign = read(diagnostic / "campaign.json", {})
            report = read(diagnostic / "diagnostic_report.json")
            summary = dict(
                cpu_only_export=True,
                target_identity=manifest["target_identity"],
                prepared_study=manifest["approved_diagnostic_configuration"],
                smoke_v4=smoke or "NOT_RUN_OR_NO_ACCEPTANCE_RECORD",
                diagnostic_started=bool(campaign),
                scheduled_trials=len(campaign.get("schedule", [])),
                attempts=len(campaign.get("attempts", [])),
                report=report,
                diagnostic_error=campaign.get("error"),
                cleanup=campaign.get("cleanup"),
                original_files_preserved=len(protected),
                test_receipt=read(release / "tests/results.json"),
                interpretation=(
                    "Scheduled trials are not a count of valid or completed observations; "
                    "retain all failures and unknowns. Smoke and diagnostics are separate; "
                    "no pooling with older targets."
                ),
            )
            (output / "RESULTS_SUMMARY.json").write_text(json.dumps(summary, indent=2) + "\n")
            request = (
                "Review this BACH TARGET_V1R3 bundle and create a next action plan only. "
                "Begin with HANDOFF/RESULTS_SUMMARY.json and "
                "docs/research/BACH_TARGET_V1R3_IMPLEMENTATION.md. Compare preparation "
                "with actual v4 smoke and 15-per-condition benign diagnostics when present; "
                "do not assume either ran or passed. Inspect validity, legitimate completion, "
                "role violations, errors, context, effect evidence and cleanup per condition. "
                "Separate unknown/missing observations from behavioral failures. Reconcile "
                "v1/v2/v3 and R2/R3 without pooling their results. Explain remaining blockers "
                "and recommend the smallest next intervention with tests and decision points. "
                "Do not weaken acceptance, inject answer keys, automatically retry, or assume "
                "authorization for concurrency, attacks, Phase 2 or Bayesian BACH.\n"
            )
            (output / "NEXT_ACTION_REQUEST.txt").write_text(request)
            bundle = output / "CHATGPT_R3_REVIEW.zip"
            with ZipFile(bundle, "w", compression=ZIP_DEFLATED, compresslevel=6) as archive:
                for path in sorted(stage.rglob("*")):
                    if path.is_file():
                        archive.write(path, str(path.relative_to(stage)))
                for path in sorted(output.iterdir()):
                    if path.is_file() and path != bundle:
                        archive.write(path, "HANDOFF/" + path.name)
        digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
        (output / "SHA256SUMS.txt").write_text(f"{digest}  {bundle.name}\n")
        print(f"PASS: CPU export validated. Attach:\n{bundle}")
        print(f"Paste:\n{output / 'NEXT_ACTION_REQUEST.txt'}")
        print("No inference launched by this export.")
    except Exception:
        print(f"Export failed; logs preserved in {output}", flush=True)
        raise


if __name__ == "__main__":
    main()
