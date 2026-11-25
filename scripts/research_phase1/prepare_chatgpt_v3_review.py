#!/usr/bin/env python3
"""Export existing smoke evidence for review; never run a model or experiment."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    repo = Path(__file__).resolve().parents[2]
    results = repo / "results/research_phase1r"
    v3 = results / "diagnostic_smoke/v3"
    review = results / "smoke_v3_cpu_review"
    python = repo / ".venv/bin/python"
    required = [
        python,
        v3 / "smoke_acceptance.json",
        review / "preservation_before.json",
        review / "review.json",
        results / "target_v1r2/tests/verify_release_export.py",
    ]
    for path in required:
        if not path.is_file():
            raise SystemExit(f"Missing required existing evidence/tool: {path}")

    original = read(review / "preservation_before.json")

    def verify_preservation():
        changed = [
            name
            for name, digest in original.items()
            if not (repo / name).is_file() or sha(repo / name) != digest
        ]
        if changed:
            raise RuntimeError(f"Original v3 evidence changed: {changed}")

    verify_preservation()
    acceptance = read(v3 / "smoke_acceptance.json")
    if acceptance["passed"] is not False:
        raise SystemExit("Expected the existing failed v3 smoke; refusing a different result")
    parent = repo / "results/chatgpt_handoffs"
    parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output = Path(tempfile.mkdtemp(prefix=f"v3-{stamp}-", dir=parent))
    print(f"CPU-only export; logs and output: {output}", flush=True)
    env = dict(os.environ)
    env.update(
        RUN_REAL="NO",
        HA_INFERENCE_MODE="local_only",
        HA_ALLOW_MODEL_DOWNLOAD="false",
        HF_HUB_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1",
    )

    def run(arguments, log):
        with (output / log).open("w") as stream:
            subprocess.run(
                [str(python), *map(str, arguments)],
                cwd=repo,
                env=env,
                stdout=stream,
                stderr=subprocess.STDOUT,
                check=True,
            )

    try:
        run(
            ["-m", "multi_agent.research_phase1r", "package", "--results-dir", results],
            "packaging.log",
        )
        archive = results / "reviewer_bundle.tar.gz"
        with tempfile.TemporaryDirectory(prefix="bach-chatgpt-review-") as tmp:
            exported = Path(tmp)
            with tarfile.open(archive) as tar:
                tar.extractall(exported, filter="data")
            run(
                [exported / "phase1r/target_v1r2/tests/verify_release_export.py", exported],
                "independent_validation.log",
            )
            exported_acceptance = read(
                exported / "phase1r/diagnostic_smoke/v3/smoke_acceptance.json"
            )
            if exported_acceptance != acceptance:
                raise RuntimeError("Export changed the smoke acceptance record")
            verify_preservation()
            convergence = read(v3 / "trials/B0-0/instrumentation/runtime_convergence.json")
            completion = read(
                v3 / "trials/B0-0/instrumentation/effects/deterministic_completion.json"
            )
            cpu_review = read(review / "review.json")
            failed = [k for k, passed in acceptance["criteria"].items() if not passed]
            summary = (
                "# BACH smoke v3 review handoff\n\n"
                f"Executed target: `{acceptance['target_identity']}`\n\n"
                "Smoke result: **FAIL**, unchanged. This export used CPU only; "
                "no inference, retry or experiment was started.\n\n"
                f"Failed acceptance criteria: {', '.join(failed)}.\n\n"
                f"Native convergence: {convergence['reason']}; "
                f"{len(convergence['native_terminal_roles'])}/4 terminal records; "
                f"missing {', '.join(convergence['missing_terminal_roles'])}; "
                f"deadline {convergence['timeout_seconds']} seconds.\n\n"
                "The packaging error was a validator comparison of raw sell versus "
                "stored SELL. The corrected validator independently matches all "
                f"{cpu_review['verified_committed_trades']} committed trades. "
                "Raw arguments and role-authorization failures are retained.\n\n"
                "Deterministic checkpoints:\n\n"
                + "\n".join(f"- {c['name']}: {c['status']}" for c in completion["checkpoints"])
                + "\n\nCleanup acceptance: "
                + str(acceptance["criteria"]["cleanup"])
                + ".\n\nPreviously recorded repository tests: "
                + cpu_review["tests"]["full_suite"]
                + ". Tests were not rerun by this export.\n\n"
                + f"Preservation check: {len(original)} original v3 files unchanged. "
                "The exported frozen-source and prompt-diff checks passed.\n\n"
                "Start with docs/research/BACH_SMOKE_V3_REVIEW.md, then the v2 "
                "root-cause report, target implementation, current findings, gate "
                "report and research roadmap. Historical plans and launch commands "
                "must be read against the latest failed smoke. Native evidence is "
                "under phase1r/diagnostic_smoke/v3; earlier failures are retained.\n"
            )
            request = (
                "Review the attached BACH smoke-v3 evidence ZIP and create the next "
                "research action plan. Begin with HANDOFF/RESULTS_SUMMARY.md and "
                "docs/research/BACH_SMOKE_V3_REVIEW.md. Distinguish the corrected "
                "CPU packaging bug from the remaining smoke failures. Reconcile "
                "v1/v2/v3 evidence and identify what is established versus uncertain. "
                "Rank the remaining blockers, cite supporting files/traces, and "
                "propose the smallest next intervention with its scientific "
                "tradeoffs, required tests, acceptance criteria, and approval needs. "
                "Treat the frozen target as as-executed; the corrected exported "
                "validator is separate CPU postprocessing. Preserve prior results. "
                "Do not prescribe benchmark answer injection, hidden-checker "
                "leakage or weakened acceptance. Do not run or assume authorization "
                "for another smoke, model/prompt/sampling/ceiling changes, "
                "calibration, concurrency, B2/B3, Phase 2 or Bayesian BACH. "
                "Provide a reviewable next action plan only.\n"
            )
            (output / "RESULTS_SUMMARY.md").write_text(summary)
            (output / "NEXT_ACTION_REQUEST.txt").write_text(request)
            receipt = dict(
                passed=True,
                cpu_only=True,
                target_identity=acceptance["target_identity"],
                smoke_still_failed=True,
                original_v3_files_preserved=len(original),
                source_archive_sha256=sha(archive),
            )
            (output / "export_verification.json").write_text(json.dumps(receipt, indent=2) + "\n")
            bundle = output / "CHATGPT_REVIEW.zip"
            with ZipFile(bundle, "w", compression=ZIP_DEFLATED, compresslevel=6) as zip_file:
                for path in sorted(exported.rglob("*")):
                    if path.is_file():
                        zip_file.write(path, str(path.relative_to(exported)))
                for path in sorted(output.iterdir()):
                    if path.is_file() and path != bundle:
                        zip_file.write(path, "HANDOFF/" + path.name)
        (output / "SHA256SUMS.txt").write_text(f"{sha(bundle)}  {bundle.name}\n")
        print(f"\nAttach this file to ChatGPT:\n{bundle}")
        print(f"\nPaste the text from:\n{output / 'NEXT_ACTION_REQUEST.txt'}")
        print(f"\nSummary:\n{output / 'RESULTS_SUMMARY.md'}")
        print("\nPASS: export validated. Smoke remains FAIL. No inference launched.")
    except Exception:
        print(f"Export failed; logs/evidence preserved in {output}", flush=True)
        raise


if __name__ == "__main__":
    main()
