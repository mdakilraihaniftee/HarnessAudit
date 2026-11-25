"""CLI for Phase 1 completion. Only isolated-* without --stub uses a model (RUN_REAL=YES)."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .common import OUT, R4_AUDIT, R4_IDENTITY, R4_RELEASE, R4_STUDY, read, rel, write


def cmd_audit(args):
    from . import audit as A
    from .discovery import cross_version_first_calls

    result = A.audit(args.study)
    summary = A.summarize(result)
    full = OUT / "r4_audit/r4_audit_full.json"
    write(full, result)
    write(OUT / "r4_audit/r4_audit_summary.json", summary)
    write(OUT / "r4_audit/cross_version_first_calls.json", cross_version_first_calls(result))
    recon = dict(
        method=(
            "Independent recomputation from raw trial evidence (native traces, CLI stderr, "
            "effect ledger, hash-checked SQLite snapshots, original checker, request log). "
            "Missing evidence -> UNVERIFIABLE."
        ),
        study=rel(args.study),
        target_identity=R4_IDENTITY,
        generated_at=time.time(),
        rows=result["reconciliation"],
        status_counts=summary["reconciliation_status"],
        mismatches=summary["mismatches"],
        full_audit=rel(full),
    )
    write(R4_AUDIT / "reconciliation.json", recon)
    print(
        json.dumps(
            dict(
                reconciliation=recon["status_counts"],
                mismatches=len(recon["mismatches"]),
                written=[rel(R4_AUDIT / "reconciliation.json"), rel(full)],
            ),
            indent=2,
        )
    )
    return 0 if not recon["mismatches"] else 1


def cmd_task_board(args):
    from .discovery import filter_matrix

    out = filter_matrix(OUT / "task_board/filter_matrix.json")
    print(
        json.dumps(
            dict(
                all_results_match_ground_truth=out["all_results_match_ground_truth"],
                queries=len(out["matrix"]),
            ),
            indent=2,
        )
    )
    return 0 if out["all_results_match_ground_truth"] else 1


def cmd_controls(args):
    from .controls_run import run_controls

    out = run_controls(args.output)
    print(json.dumps(out, indent=2))
    return 0 if out["passed"] else 1


def cmd_legal(args):
    import asyncio
    import uuid

    from .controls_run import legal_workflow

    root = Path(args.output or OUT / "legal_workflow" / uuid.uuid4().hex[:12])
    if root.exists():
        raise ValueError("Use a new directory")
    out = asyncio.run(legal_workflow(root))
    print(json.dumps({k: v for k, v in out.items() if k != "steps"}, indent=2))
    return 0 if out["passed"] else 1


def cmd_isolated(args):
    from . import isolated

    out = isolated.run(
        args.test,
        Path(args.output),
        repeats=args.repeats,
        parallel=args.parallel,
        gpu_ids=args.gpu_ids,
        stub=args.stub,
    )
    print(json.dumps(dict(arms=out["arms"], campaign_error=out.get("campaign_error")), indent=2))
    return 0 if not out.get("campaign_error") else 1


def cmd_analyze_isolated(args):
    from .isolated import summarize

    out = summarize(Path(args.root))
    print(json.dumps(out["arms"], indent=2))
    return 0


def cmd_gates(args):
    from . import audit as A
    from .gates import write_gates

    result = read(OUT / "r4_audit/r4_audit_full.json") or A.audit()
    controls = read(OUT / "controls_latest.json")
    out = write_gates(result, controls)
    print(json.dumps(dict(status=out["status"], assessment=out["assessment"]), indent=2))
    return 0


def cmd_identity(args):
    """Read-only: recompute TARGET_V1R4 identity without writing into the release folder."""
    from multi_agent.research_phase1r.controls import task
    from multi_agent.research_phase1r.diagnostic import configuration
    from multi_agent.research_phase1r.r4_release import verify_preservation
    from multi_agent.research_phase1r.target import target_identity

    current = target_identity(task(), configuration([0, 1, 2, 3]), version="TARGET_V1R4")
    frozen = read(R4_RELEASE / "TARGET_V1R4.json", {})
    out = dict(
        frozen=frozen.get("target_identity"),
        current=current["target_identity"],
        matches=frozen.get("target_identity") == current["target_identity"] == R4_IDENTITY,
        r4_preservation=verify_preservation()["preserved"],
        phase1c_preservation=preservation()["preserved"],
    )
    print(json.dumps(out, indent=2))
    return 0 if out["matches"] and out["r4_preservation"] and out["phase1c_preservation"] else 1


def preservation(receipt=None):
    import hashlib

    from .common import REPO

    before = read(receipt or OUT / "preservation_before.json", {})
    changed, missing = [], []
    for name, sha in (before.get("files") or {}).items():
        p = REPO / name
        if not p.is_file():
            missing.append(name)
        elif hashlib.sha256(p.read_bytes()).hexdigest() != sha:
            changed.append(name)
    return dict(
        protected_files=len(before.get("files") or {}),
        changed=changed,
        missing=missing,
        preserved=bool(before.get("files")) and not changed and not missing,
    )


def cmd_preservation(args):
    out = preservation()
    write(OUT / "preservation_check.json", dict(out, checked_at=time.time()))
    print(
        json.dumps(
            {k: (v if not isinstance(v, list) else v[:20]) for k, v in out.items()}, indent=2
        )
    )
    return 0 if out["preserved"] else 1


def cmd_gpu_preflight(args):
    from multi_agent.research_phase1r.diagnostic import parse_ids, preflight

    ids = parse_ids(args.gpu_ids)
    out = preflight(ids)
    path = OUT / "gpu_preflight" / f"{int(time.time())}.json"
    write(path, out)
    print(json.dumps(dict(written=rel(path), gpu_ids=ids), indent=2))
    return 0


def cmd_audit_t2t3(args):
    from . import t2t3

    out = t2t3.audit()
    print(
        json.dumps(
            dict(
                status=out["status"],
                mismatches=len(out["mismatches"]),
                written=rel(OUT / "t2t3/t2_t3_reconciliation.json"),
            ),
            indent=2,
        )
    )
    return 0 if not out["mismatches"] else 1


def cmd_analyze_t2(args):
    from . import t2t3

    out = t2t3.t2_corrected(args.root) if args.root else t2t3.t2_corrected()
    print(
        json.dumps(
            dict(
                by_schema=out["by_schema"],
                wording_invariance=dict(
                    original_neutral=out["wording"]["original_neutral_semantic_invariance"][
                        "invariant"
                    ],
                    urgency_neutral=out["wording"]["corrected_urgency_semantic_invariance"][
                        "invariant"
                    ],
                ),
            ),
            indent=2,
        )
    )
    return 0


def cmd_validate_t3_context(args):
    """CPU: fixture preflight for every role + real Qwen Code CLI against a stub."""
    import uuid

    from . import isolated

    root = (
        OUT / "t3c_validation" / f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-"
        f"{uuid.uuid4().hex[:6]}"
    )
    pre = isolated.fixture_preflight(root / "fixture_preflight")
    stub = isolated.run(
        "specialists_corrected", root / "stub_run", repeats=1, parallel=4, stub=True
    )
    sessions = [s.get("t3c") or {} for s in stub["sessions"]]
    out = dict(
        root=rel(root),
        mode="CPU_ONLY (fixture preflight + real Qwen Code CLI against a stub endpoint; "
        "no model inference)",
        fixture_identity=pre["fixture_identity"],
        fixture_preflight={
            r: dict(valid=v["valid"], checks=v["checks"], prepared_state=v["prepared_state"])
            for r, v in pre["roles"].items()
        },
        stub_campaign_error=stub.get("campaign_error"),
        model_visible_context_in_trace={
            s.get("role"): s.get("context_complete") for s in sessions
        },
        context_seen_via={s.get("role"): s.get("context_seen_via") for s in sessions},
        passed=pre["all_valid"]
        and stub.get("campaign_error") is None
        and all(s.get("context_complete") for s in sessions)
        and len(sessions) == 3,
    )
    write(root / "t3c_context_validation.json", out)
    write(OUT / "t3c_validation/latest.json", out)
    print(json.dumps({k: v for k, v in out.items() if k != "fixture_preflight"}, indent=2))
    return 0 if out["passed"] else 1


def cmd_t3c_ready(args):
    """Refuse the full T3C diagnostic unless a structurally valid real smoke exists."""
    root = Path(args.smoke)
    plan, camp = read(root / "plan.json", {}), read(root / "campaign.json", {})
    sessions = [read(p, {}) for p in sorted((root / "sessions").glob("*/session.json"))]
    ok = (
        plan.get("test") == "specialists_corrected"
        and plan.get("mode") == "REAL_LOCAL"
        and camp.get("error") is None
        and len(sessions) == len(plan.get("sessions", []))
        and all(s.get("diagnostic_valid") is True for s in sessions)
        and all(s.get("model_started") for s in sessions)
    )
    print(json.dumps(dict(smoke=rel(root), structurally_valid=ok), indent=2))
    return 0 if ok else 1


def cmd_package(args):
    from .bundle import build

    out = build()
    print(json.dumps(out, indent=2))
    return 0 if out["validation"]["ok"] else 1


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m multi_agent.research_phase1c")
    sub = p.add_subparsers(dest="command", required=True)
    a = sub.add_parser("audit-r4")
    a.add_argument("--study", type=Path, default=R4_STUDY)
    a.set_defaults(fn=cmd_audit)
    sub.add_parser("test-task-board").set_defaults(fn=cmd_task_board)
    c = sub.add_parser("test-instrumentation")
    c.add_argument("--output", type=Path)
    c.set_defaults(fn=cmd_controls)
    lw = sub.add_parser("test-scripted-workflow")
    lw.add_argument("--output", type=Path)
    lw.set_defaults(fn=cmd_legal)
    i = sub.add_parser("isolated")
    i.add_argument(
        "--test",
        choices=["coordinator", "specialists", "specialists_corrected", "coordinator_urgency"],
        required=True,
    )
    i.add_argument("--output", required=True)
    i.add_argument("--repeats", type=int, default=5)
    i.add_argument("--parallel", type=int, default=4)
    i.add_argument("--gpu-ids", default="0,1,2,3")
    i.add_argument("--stub", action="store_true", help="CPU plumbing stub; no model")
    i.set_defaults(fn=cmd_isolated)
    ai = sub.add_parser("analyze-isolated")
    ai.add_argument("--root", required=True)
    ai.set_defaults(fn=cmd_analyze_isolated)
    sub.add_parser("evaluate-gates").set_defaults(fn=cmd_gates)
    sub.add_parser("identity-check").set_defaults(fn=cmd_identity)
    sub.add_parser("verify-preservation").set_defaults(fn=cmd_preservation)
    g = sub.add_parser("gpu-preflight")
    g.add_argument("--gpu-ids", default="0,1,2,3")
    g.set_defaults(fn=cmd_gpu_preflight)
    sub.add_parser("audit-t2-t3").set_defaults(fn=cmd_audit_t2t3)
    a2 = sub.add_parser("analyze-t2")
    a2.add_argument("--root", type=Path)
    a2.set_defaults(fn=cmd_analyze_t2)
    sub.add_parser("validate-t3-context").set_defaults(fn=cmd_validate_t3_context)
    rd = sub.add_parser("t3c-ready")
    rd.add_argument("--smoke", required=True)
    rd.set_defaults(fn=cmd_t3c_ready)
    sub.add_parser("package").set_defaults(fn=cmd_package)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
