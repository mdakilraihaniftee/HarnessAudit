"""Coherent manual Phase 1R interface; CPU commands never start inference."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .audit import reconstruct, write


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "command",
        choices=[
            "preflight",
            "audit-original",
            "test-instrumentation",
            "test-scripted-harness",
            "gpu-preflight",
            "smoke",
            "diagnostic",
            "benchmark",
            "analyze",
            "evaluate-gates",
            "package",
            "worker",
            "review-smoke",
            "prepare-target",
            "audit-trial",
            "test-tool-contract",
            "freeze-r4",
            "preflight-r4",
            "verify-preservation",
            "benign-study",
            "study-summary",
        ],
    )
    p.add_argument(
        "--results-dir", default=os.environ.get("OUTPUT_DIR", "results/research_phase1r")
    )
    p.add_argument("--pilot-dir", default="results/research_phase1/pilot_15_real")
    p.add_argument("--controls-dir", default="results/research_phase1r/instrumentation_controls")
    p.add_argument("--smoke-results-dir", default="results/research_phase1r/diagnostic_smoke/v1")
    p.add_argument(
        "--target-version",
        choices=["TARGET_V1R", "TARGET_V1R2", "TARGET_V1R3", "TARGET_V1R4"],
        default="TARGET_V1R",
    )
    p.add_argument("--target-manifest")
    p.add_argument("--review-campaign", default="results/research_phase1r/diagnostic_smoke/v2")
    p.add_argument("--gpu-ids", default=os.environ.get("GPU_IDS", "0,1,2,3"))
    p.add_argument("--repeats", type=int, default=int(os.environ.get("REPEATS", "10")))
    p.add_argument("--master-seed", type=int, default=int(os.environ.get("MASTER_SEED", "0")))
    p.add_argument("--parallel", type=int, default=int(os.environ.get("MAX_PARALLEL_TRIALS", "2")))
    p.add_argument("--server-port", type=int, default=int(os.environ.get("SERVER_PORT", "8000")))
    p.add_argument("--proxy-port", type=int, default=int(os.environ.get("PROXY_PORT", "18081")))
    p.add_argument(
        "--model-request-budget",
        type=int,
        default=int(os.environ.get("MODEL_REQUEST_BUDGET", "12000")),
    )
    p.add_argument(
        "--per-trial-requests",
        type=int,
        default=int(os.environ.get("MODEL_REQUESTS_PER_TRIAL", "400")),
    )
    p.add_argument(
        "--agent-timeout", type=int, default=int(os.environ.get("AGENT_TIMEOUT", "300"))
    )
    p.add_argument("--max-turns", type=int, default=int(os.environ.get("MAX_TURNS", "30")))
    p.add_argument("--spec", type=Path)
    p.add_argument("--trial-dir", type=Path)
    args = p.parse_args(argv)
    root = Path(args.results_dir)
    try:
        if args.command == "review-smoke":
            from .smoke_review import review_smoke

            result = review_smoke(args.review_campaign, root)
            print(json.dumps({"mode": result["mode"], "report": str(root / "review.json")}))
            return 0
        if args.command == "audit-trial":
            from .coordination_audit import audit_trial

            if args.trial_dir is None:
                raise ValueError("audit-trial requires --trial-dir")
            result = audit_trial(args.trial_dir, root)
            summary = result["coordinator_call_ledger"]["summary"]
            print(
                json.dumps(
                    dict(
                        output=str(root),
                        coordinator_calls=summary["tool_call_count"],
                        rejected=summary["rejected_tool_invocations"],
                        empty=summary["empty_responses"],
                        stop_reason=summary["stop_reason"],
                        effects={
                            k: result["effect_attribution"][k]
                            for k in [
                                "attempted_role_violations",
                                "verified_unauthorized_effects",
                                "failed_unauthorized_attempts",
                                "valid_authorized_effects",
                                "effects_with_unknown_attribution",
                            ]
                        },
                    ),
                    indent=2,
                )
            )
            return 0
        if args.command in {"freeze-r4", "preflight-r4", "verify-preservation"}:
            from . import r4_release
            from .diagnostic import configuration

            if args.command == "verify-preservation":
                result = r4_release.verify_preservation()
                print(json.dumps({k: v for k, v in result.items()}, indent=2))
                return 0 if result["preserved"] else 1
            cfg = configuration([0, 1, 2, 3], args.server_port)
            if args.command == "freeze-r4":
                print(r4_release.freeze(cfg)["target_identity"])
                return 0
            result = r4_release.preflight(cfg)
            print(r4_release.json_dump(result))
            return 0 if result["ready_for_manual_smoke"] else 1
        if args.command == "test-tool-contract":
            from .tool_contract import schema_comparison

            result = schema_comparison()
            write(root / "tool_schema_comparison.json", result)
            print(
                json.dumps(
                    dict(
                        identical_tool_set=result["identical_tool_set"],
                        changed=[
                            f"{c['tool']}.{c.get('parameter') or c.get('field')}"
                            for c in result["changes"]
                        ],
                        model_visible_schema_sha256=result["model_visible_schema_sha256"],
                    ),
                    indent=2,
                )
            )
            return 0 if result["identical_tool_set"] else 1
        if args.command == "prepare-target":
            from .diagnostic import configuration
            from .release import prepare_target

            result = prepare_target(
                root,
                configuration([0, 1, 2, 3], args.server_port),
                version="TARGET_V1R2"
                if args.target_version == "TARGET_V1R"
                else args.target_version,
            )
            print(result["target_identity"])
            return 0
        if args.command == "audit-original":
            return 0 if reconstruct(args.pilot_dir, root / "original_pilot_audit") else 2
        if args.command == "preflight":
            from multi_agent.research_phase1.experiment import git_state, versions

            from .controls import task
            from .diagnostic import configuration
            from .lifecycle import identity
            from .target import target_identity

            result = dict(
                git=git_state(),
                versions=versions(),
                required_docs={
                    n: (Path("docs/research") / n).exists()
                    for n in [
                        "PHASE_1_REPORT.md",
                        "PHASE_1_LAUNCHER_REPORT.md",
                        "BACH_COMPLETE_RESEARCH_OVERVIEW.md",
                        "BACH_CURRENT_FINDINGS.md",
                        "BACH_NEXT_RESEARCH_ROADMAP.md",
                    ]
                },
                historical_pid=3202726,
                historical_pid_now=identity(3202726),
                historical_pid_policy=(
                    "Never signal from historical numeric PID. Compare saved start "
                    "time/command if present."
                ),
                cpu_only=True,
            )
            write(root / "preflight.json", result)
            cfg = configuration([0, 1, 2, 3])
            write(
                root / "target_versions/TARGET_V1.json",
                target_identity(task(), cfg, version="TARGET_V1", original_pilot=args.pilot_dir),
            )
            write(root / "target_versions/TARGET_V1R.json", target_identity(task(), cfg))
        elif args.command == "test-instrumentation":
            from .controls import offline

            result = offline(Path(args.controls_dir))
            print(json.dumps({"passed": result["passed"], "mode": result["mode"]}))
            return 0 if result["passed"] else 1
        elif args.command == "test-scripted-harness":
            from .controls import run_scripted

            result = run_scripted(Path(args.controls_dir))
            print(json.dumps(result))
            return 0 if result["passed"] else 1
        elif args.command == "gpu-preflight":
            from multi_agent.research_phase1.gpu import parse_ids

            from .diagnostic import preflight

            result = preflight(parse_ids(args.gpu_ids), args.server_port)
            write(root / "gpu_preflight.json", result)
            print(json.dumps(result, indent=2))
        elif args.command == "study-summary":
            from .r4_release import study_summary

            result = study_summary(root)
            print(json.dumps({k: v for k, v in result.items() if k != "trials"}, indent=2))
        elif args.command in {"smoke", "diagnostic", "benchmark", "benign-study"}:
            from .diagnostic import run

            if (
                args.command == "smoke"
                and "--repeats" not in (argv or sys.argv[1:])
                and "REPEATS" not in os.environ
            ):
                args.repeats = 1
            return run(args)
        elif args.command == "worker":
            from .diagnostic import worker

            if args.spec is None:
                raise ValueError("Worker requires --spec")
            return worker(args.spec)
        else:
            from .reporting import analyze, evaluate_gates, package

            if args.command == "analyze":
                result = analyze(root)[0]
            elif args.command == "evaluate-gates":
                result = evaluate_gates(root, Path(args.controls_dir))
            else:
                result = {"archive": str(package(root, args.pilot_dir))}
            print(json.dumps(result, indent=2))
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Phase 1R: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
