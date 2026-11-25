"""Reviewer bundle (pre-Phase-2 final), plus recorded CPU test runs."""

from __future__ import annotations

import hashlib
import json
import secrets
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

from .common import OUT, R4_AUDIT, R4_IDENTITY, R4_RELEASE, R4_STUDY, REPO, read, rel, write

EXCLUDED_PARTS = {
    "source",
    "workspaces",
    "qwen_home",
    ".control",
    "__pycache__",
    "home",
    "ws",
    "clawteam",
}
EXCLUDED_NAMES = {"mcp.json", "private_server_spec.json", "reviewer_bundle.tar.gz"}
EXCLUDED_SUFFIXES = {".safetensors", ".gguf", ".pt", ".pth", ".ckpt"}
VALIDATOR = REPO / "scripts/research_phase1/validate_phase1_completion_bundle.py"
DOCS = [
    "BACH_PHASE1_COMPLETION_AUDIT.md",
    "BACH_R4_POSTRUN_AUDIT.md",
    "BACH_TASK_DISCOVERY_ROOT_CAUSE.md",
    "BACH_SPECIALIST_EXECUTION_ANALYSIS.md",
    "BACH_COORDINATION_REPAIR_IMPLEMENTATION.md",
    "BACH_TARGET_VERSION_COMPARISON.md",
    "BACH_LEGITIMATE_COMPLETION_ANALYSIS.md",
    "BACH_EFFECT_MEASUREMENT_VALIDATION.md",
    "BACH_PHASE1_FINAL_GATE_REPORT.md",
    "BACH_PHASE1_MANUAL_RUN_GUIDE.md",
    "BACH_PHASE1_TO_PHASE2_DECISION.md",
    "BACH_T2_T3_INDEPENDENT_AUDIT.md",
    "BACH_T2_T3_FINAL_AUDIT.md",
    "BACH_T3_CONTEXT_REPAIR.md",
    "BACH_T3_CAPABILITY_RESULTS.md",
    "BACH_T2_CORRECTED_INTERPRETATION.md",
    "BACH_TARGET_CAPABILITY_DECISION.md",
    "BACH_PHASE1_FINAL_READINESS.md",
    "BACH_FINAL_MANUAL_RUN_GUIDE.md",
    "BACH_PHASE2_ENTRY_DECISION.md",
]
BUNDLE_NAME = "CHATGPT_BACH_PRE_PHASE2_FINAL"
SOURCES = [
    *sorted(
        str(p.relative_to(REPO)) for p in (REPO / "multi_agent/research_phase1c").glob("*.py")
    ),
    "scripts/research_phase1/phase1_completion.sh",
    "scripts/research_phase1/validate_phase1_completion_bundle.py",
    "tests/test_phase1_completion.py",
    "multi_agent/research_phase1r/tool_contract.py",
    "multi_agent/research_phase1r/target.py",
    "multi_agent/frameworks/clawteam/tasks.py",
    "vendor/clawteam/clawteam/store/file.py",
    "vendor/clawteam/clawteam/team/models.py",
]
TESTS = [
    ("00_full_suite", [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]),
    (
        "01_phase1_completion",
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "tests/test_phase1_completion.py",
        ],
    ),
    (
        "02_phase1r4",
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "tests/test_research_phase1r4.py",
        ],
    ),
    (
        "03_ruff",
        [
            str(Path(sys.executable).parent / "ruff"),
            "check",
            "multi_agent/research_phase1c",
            "tests/test_phase1_completion.py",
            "scripts/research_phase1/validate_phase1_completion_bundle.py",
        ],
    ),
    (
        "04_compileall",
        [
            sys.executable,
            "-W",
            "error",
            "-m",
            "compileall",
            "-q",
            "multi_agent/research_phase1c",
            "scripts/research_phase1/validate_phase1_completion_bundle.py",
        ],
    ),
    ("05_bash_syntax", ["bash", "-n", "scripts/research_phase1/phase1_completion.sh"]),
    ("06_git_diff_check", ["git", "diff", "--check"]),
]


def record_tests(root=None) -> dict:
    root = Path(root or OUT / "tests")
    root.mkdir(parents=True, exist_ok=True)
    rows = []
    for name, cmd in TESTS:
        started = time.time()
        proc = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=3600)
        log = root / f"{name}.log"
        log.write_text(f"$ {' '.join(cmd)}\n\n{proc.stdout}\n{proc.stderr}")
        tail = (proc.stdout.strip().splitlines() or [""])[-1]
        rows.append(
            dict(
                name=name,
                command=" ".join(rel(c) if c.startswith("/") else c for c in cmd),
                exit_code=proc.returncode,
                passed=proc.returncode == 0,
                summary_line=tail[:300],
                seconds=round(time.time() - started, 1),
                log=rel(log),
            )
        )
    out = dict(recorded_at=time.time(), results=rows, all_passed=all(r["passed"] for r in rows))
    write(root / "results.json", out)
    return out


def _skip(path: Path) -> bool:
    return (
        path.name in EXCLUDED_NAMES
        or path.suffix in EXCLUDED_SUFFIXES
        or any(part in EXCLUDED_PARTS for part in path.parts)
    )


def _add_tree(files: dict, src: Path, dest: str):
    if not src.exists():
        return
    for p in sorted(src.rglob("*")):
        if p.is_file():
            r = p.relative_to(src)
            if not _skip(r):
                files[f"{dest}/{r}"] = p


def isolated_runs() -> list:
    runs = []
    for plan in sorted((OUT / "isolated").glob("*/plan.json")):
        p = read(plan, {})
        runs.append(dict(root=rel(plan.parent), mode=p.get("mode"), test=p.get("test")))
    return runs


def results_summary(
    gates: dict, audit_summary: dict, controls: dict, tests: dict, board: dict
) -> dict:
    iso = isolated_runs()
    real_iso = [r for r in iso if r["mode"] == "REAL_LOCAL"]
    t3c_real = [r["root"] for r in real_iso if r["test"] == "specialists_corrected"]
    return dict(
        bundle=BUNDLE_NAME,
        created_at=time.time(),
        git_head=subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True
        ).stdout.strip(),
        target_identities=dict(
            current_frozen=R4_IDENTITY,
            new_target_created=False,
            reason=(
                "No implementation or schema defect was found; the remaining failures are "
                "model-generated queries and role behaviour (stop conditions 1 and 8)."
            ),
            historical=read(R4_RELEASE / "release_status.json", {}).get("historical_identities"),
        ),
        new_real_inference_in_this_phase=bool(real_iso),
        new_real_inference_runs=real_iso,
        statement=(
            "No real inference evidence is included."
            if not real_iso
            else "Real isolated diagnostics are included under evidence/isolated: "
            + ", ".join(f"{r['test']} ({r['root'].split('/')[-1]})" for r in real_iso)
            + ". Corrected T3 (T3C) real runs: "
            + ("NOT_EXECUTED" if not t3c_real else "included")
            + "."
        ),
        t3c_real_status="NOT_EXECUTED" if not t3c_real else "EXECUTED",
        t3c_real_runs=t3c_real,
        diagnostic_fixture_identity=(read(OUT / "t3c_validation/latest.json") or {}).get(
            "fixture_identity"
        ),
        t3c_cpu_context_validation=(read(OUT / "t3c_validation/latest.json") or {}).get(
            "model_visible_context_in_trace"
        ),
        t2_corrected=_t2_digest(),
        t3_original=_t3_digest(),
        isolated_diagnostics=gates.get("isolated_diagnostics"),
        recommended_pi_decision=RECOMMENDED_PI_DECISION,
        phase1_status="PARTIALLY_COMPLETE",
        readiness=gates["assessment"],
        gates=gates["status"],
        phase2_ready=gates["phase2_ready"],
        r4_reconciliation=audit_summary["reconciliation_status"],
        r4_key_counts=dict(
            coordinator_first_call_variants=audit_summary["coordinator_first_call_variants"],
            coordinator_labels=audit_summary["coordinator_labels"],
            effect_classes=audit_summary["effect_classes"],
            specialists=audit_summary["specialists"],
            authorized_risk_officer_requests={
                k: v
                for k, v in audit_summary["authorized_risk_officer_requests"].items()
                if k != "requested_values"
            },
            trader_attempts_without_effect={
                k: v
                for k, v in audit_summary["trader_attempts_without_effect"].items()
                if k != "rows"
            },
            checkpoints=audit_summary["checkpoints"],
            financial_state_completion=audit_summary["financial_state_completion"],
            role_faithful_completion=audit_summary["role_faithful_completion"],
        ),
        task_board_matrix_matches_ground_truth=board.get("all_results_match_ground_truth"),
        controls=dict(
            g1=controls.get("g1_instrumentation_pass"),
            legal_workflow=controls.get("legal_workflow"),
        ),
        tests={
            r["name"]: dict(passed=r["passed"], summary=r["summary_line"])
            for r in tests.get("results", [])
        },
        layers=dict(
            IMPLEMENTED=[
                "observation-only coordination labels",
                "independent R4 audit",
                "task-board filter matrix",
                "scripted legal workflow",
                "isolated T2/T3 Qwen runners",
                "G1-G8 evaluator",
                "bundle+validator",
                "phase1_completion.sh",
            ],
            CPU_TESTED=["all of the above (tests/test_phase1_completion.py)"],
            SCRIPTED_INTEGRATION_TESTED=[
                "controls (4/4 positives, 0 negative matches)",
                "legal workflow core completion",
                "isolated runners with real Qwen CLI against a stub",
            ],
            LIVE_QWEN_TESTED=[r["root"] for r in real_iso],
            NOT_RUN=[
                "T3C corrected specialist smoke/diagnostic" if not t3c_real else None,
                "optional T2 urgency follow-up",
                "B0 smoke",
                "v5 smoke",
                "45-trial calibration",
                "concurrency 2/4",
            ],
        ),
    )


RECOMMENDED_PI_DECISION = (
    "C now (run the corrected T3C smoke, then the 15-session T3C diagnostic), then choose "
    "A or B from its pre-specified reading; D if a working target still saturates O1."
)


def _t2_digest():
    t2 = read(OUT / "t2t3/t2_corrected_analysis.json")
    if not t2:
        return None
    keys = (
        "n",
        "high_priority_first_task_list",
        "all_assignments_visible_via_task_list",
        "all_assignments_visible",
        "all_assignments_identified",
        "relevant_assignment_acted_on",
        "downstream_progress",
        "sessions_using_board_team",
        "context_overflow",
    )
    return dict(
        by_schema={s: {k: v[k] for k in keys} for s, v in t2["by_schema"].items()},
        by_arm={a: {k: v[k] for k in keys} for a, v in t2["by_arm"].items()},
        wording_invariance=dict(
            original_neutral=t2["wording"]["original_neutral_semantic_invariance"]["invariant"],
            urgency_neutral=t2["wording"]["corrected_urgency_semantic_invariance"]["invariant"],
        ),
        requests=dict(
            total=t2["requests"].get("total"),
            context_overflow=len(t2["requests"].get("context_overflow", [])),
        ),
    )


def _t3_digest():
    t3 = read(OUT / "t2t3/t3_original_audit.json")
    return (
        None
        if not t3
        else dict(
            verdict=t3["verdict"], by_role=t3["by_role"], requests=t3["requests"].get("total")
        )
    )


def build() -> dict:
    gates = read(OUT / "gates/phase1_gate_report.json")
    audit_summary = read(OUT / "r4_audit/r4_audit_summary.json")
    controls = read(OUT / "controls_latest.json") or {}
    tests = read(OUT / "tests/results.json") or {}
    board = read(OUT / "task_board/filter_matrix.json") or {}
    if not gates or not audit_summary:
        raise ValueError("Run audit-r4 and evaluate-gates before packaging")
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    dest = REPO / "results/chatgpt_handoffs" / f"pre_phase2_final_{stamp}-{secrets.token_hex(4)}"
    dest.mkdir(parents=True)
    files: dict[str, Path] = {}
    for d in DOCS:
        files[f"docs/{d}"] = REPO / "docs/research" / d
    for s in SOURCES:
        files[f"source/{s}"] = REPO / s
    _add_tree(files, R4_STUDY, "evidence/r4_study")
    _add_tree(files, R4_AUDIT, "evidence/r4_audit/reconciliation")
    _add_tree(files, OUT / "r4_audit", "evidence/r4_audit/phase1c")
    _add_tree(files, OUT / "task_board", "evidence/task_board")
    _add_tree(files, OUT / "gates", "evidence/gates")
    _add_tree(files, OUT / "tests", "evidence/tests")
    _add_tree(files, OUT / "t2t3", "evidence/t2t3")
    latest = read(OUT / "t3c_validation/latest.json")
    if latest:
        files["evidence/t3c_validation/latest.json"] = OUT / "t3c_validation/latest.json"
        _add_tree(files, REPO / latest["root"], "evidence/t3c_validation/run")
    if controls.get("root"):
        _add_tree(files, REPO / controls["root"], "evidence/controls")
    for run in isolated_runs():
        _add_tree(files, REPO / run["root"], f"evidence/isolated/{Path(run['root']).name}")
    files["evidence/target_v1r4/TARGET_V1R4.json"] = R4_RELEASE / "TARGET_V1R4.json"
    _add_tree(files, R4_RELEASE / "schema_evidence", "evidence/target_v1r4/schema_evidence")
    files["evidence/target_v1r4/cross_version_coordinator_ledgers.json"] = (
        R4_RELEASE / "cross_version_coordinator_ledgers.json"
    )
    files["validate_bundle.py"] = VALIDATOR
    missing = [k for k, v in files.items() if not v.exists()]
    if missing:
        raise ValueError(f"Missing bundle inputs: {missing[:10]}")
    summary = results_summary(gates, audit_summary, controls, tests, board)
    staging = dest / "staging"
    write(staging / "HANDOFF/RESULTS_SUMMARY.json", summary)
    (staging / "HANDOFF/README.md").parent.mkdir(parents=True, exist_ok=True)
    (staging / "HANDOFF/README.md").write_text(
        "# BACH pre-Phase-2 final — reviewer bundle\n\n"
        "Start with HANDOFF/RESULTS_SUMMARY.json, then docs/BACH_TARGET_CAPABILITY_DECISION.md"
        " and docs/BACH_T2_T3_FINAL_AUDIT.md.\n"
        "Run `python3 validate_bundle.py .` from the unzipped folder (standard library only).\n"
        f"{summary['statement']}\n"
    )
    files["HANDOFF/RESULTS_SUMMARY.json"] = staging / "HANDOFF/RESULTS_SUMMARY.json"
    files["HANDOFF/README.md"] = staging / "HANDOFF/README.md"
    manifest = {
        k: dict(sha256=hashlib.sha256(v.read_bytes()).hexdigest(), bytes=v.stat().st_size)
        for k, v in sorted(files.items())
    }
    write(
        staging / "MANIFEST.json",
        dict(
            files=manifest,
            excluded=dict(
                parts=sorted(EXCLUDED_PARTS),
                names=sorted(EXCLUDED_NAMES),
                suffixes=sorted(EXCLUDED_SUFFIXES),
            ),
        ),
    )
    files["MANIFEST.json"] = staging / "MANIFEST.json"
    zpath = dest / f"{BUNDLE_NAME}.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for name, src in sorted(files.items()):
            z.write(src, name)
    shutil.rmtree(staging)
    check = subprocess.run(
        [sys.executable, "-I", str(VALIDATOR), str(zpath)],
        capture_output=True,
        text=True,
        timeout=1800,
    )
    try:
        validation = json.loads(check.stdout)
    except json.JSONDecodeError:
        validation = dict(ok=False, stdout=check.stdout[-2000:], stderr=check.stderr[-2000:])
    write(dest / "validation_receipt.json", validation)
    return dict(
        zip=rel(zpath),
        files=len(files),
        bytes=zpath.stat().st_size,
        validation=dict(ok=validation.get("ok"), failures=validation.get("failures")),
    )
