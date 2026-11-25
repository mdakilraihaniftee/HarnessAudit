"""Collect immutable per-process trials into one independently inspectable bundle."""

from __future__ import annotations

import hashlib
import io
import json
import os
import socket
import tarfile
import tempfile
from pathlib import Path

from .experiment import REPO_ROOT, implementation_sources, read_json, save
from .reporting import analyze, csv_write, sanitize
from .review_bundle import descriptive, validate


def collect(root, ledger):
    rows, observations = [], []
    rules = read_json(root / "rule_catalog.json")["rules"]
    for attempt in ledger["attempts"]:
        child = root / "executions" / attempt["slot"]
        row, obs = None, None
        if (child / "run_manifest.json").exists() and attempt.get("status") not in {
            "running",
            "reserved",
        }:
            try:
                analyze(
                    child
                )  # Original integrity checks, without rewriting original trial evidence.
                summaries = list(child.glob("trials/*/trial.json"))
                if len(summaries) == 1:
                    row = read_json(summaries[0])
                    obs = read_json(summaries[0].parent / "rule_observations.json")["observations"]
                    report = read_json(child / "report.json")
                    if report["artifact_errors"]:
                        row = None
            except (OSError, ValueError, KeyError, TypeError):
                row = None
        if row is None:
            row = {
                "trial_id": attempt["reservation_id"],
                "condition": attempt["condition"],
                "mode": ledger["configuration"]["mode"],
                "execution_status": "failed_or_incomplete",
                "execution_error": attempt.get("error") or "Missing or invalid worker evidence",
                "task_completion": None,
                "forbidden_violation_count": None,
            }
            obs = [
                {
                    "trial_id": row["trial_id"],
                    "rule_id": r["rule_id"],
                    "rule_type": r["rule_type"],
                    "agent_role": r["agent_role"],
                    "research_classification": r["research_classification"],
                    "violation_observed": None,
                    "attempt_observed": None,
                    "effect_observed": None,
                    "event_references": [],
                    "evidence_quality": "unknown_missing_worker_evidence",
                }
                for r in rules
            ]
        row = dict(
            row,
            pilot_id=ledger["pilot_id"],
            pilot_slot=attempt["slot"],
            bundle_artifact_prefix="executions/" + attempt["slot"],
            scheduler_attempt=attempt,
        )
        rows.append(row)
        observations.extend(obs)
    return rows, observations


def target_evidence(root, row, requests):
    child = root / row["bundle_artifact_prefix"]
    real = row.get("mode") == "REAL_LOCAL"
    started, ended = False, False
    for path in child.glob("trials/*/traces/**/*.jsonl"):
        try:
            events = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
            started |= any(e.get("type") == "trace_start" for e in events)
            ended |= any(e.get("type") == "trace_end" and e.get("error") is None for e in events)
        except (OSError, ValueError):
            pass
    native = (row.get("original_harnessaudit_metrics") or {}).get("inference_provenance") or {}
    agents = (native.get("native_summary") or {}).get("agents") or {}
    native_complete = len(agents) == 4 and all(
        a.get("result_subtype") == "success" for a in agents.values()
    )
    model_requests = requests.get("by_trial", {}).get(row["pilot_slot"], 0)
    return {
        "actual_target_started": bool(real and started and model_requests > 0),
        "actual_target_completed": bool(real and ended and native_complete and model_requests > 0),
        "gateway_model_request_attempts": model_requests,
        "native_model_request_count": row.get("model_request_count"),
    }


def report(root, ledger):
    rows, observations = collect(root, ledger)
    requests = (
        read_json(root / "model_requests.json") if (root / "model_requests.json").exists() else {}
    )
    for row in rows:
        row.update(target_evidence(root, row, requests))
    stats = descriptive(rows)
    intended = 3 * ledger["configuration"]["repeats"]
    completed = sum(r["actual_target_completed"] for r in rows)
    status = {
        "pilot_id": ledger["pilot_id"],
        "mode": ledger["configuration"]["mode"],
        "planned_target_executions": intended,
        "reserved_attempts": len(rows),
        "actual_target_executions_started": sum(r["actual_target_started"] for r in rows),
        "actual_target_executions_completed": completed,
        "all_15_real_targets_completed": completed == intended and intended >= 15,
        "task_completion_is_distinct_from_execution_completion": True,
        "model_request_attempts": requests.get("used", 0),
        "model_request_maximum": ledger["configuration"]["model_request_budget"],
        "valid_research_executions": sum(
            c["valid_research_executions"] for c in stats["conditions"].values()
        ),
        "errors": ledger.get("errors", []),
        "cleanup": ledger.get("cleanup", {}),
        "descriptive_statistics": stats,
    }
    save(root / "run_status.json", status)
    save(root / "report.json", status)
    save(root / "datasets/trials.json", {"trials": rows})
    save(root / "datasets/rule_observations.json", {"observations": observations})
    csv_write(root / "trial_summary.csv", rows, ["trial_id", "condition", "execution_status"])
    csv_write(root / "rule_observations.csv", observations, ["trial_id", "rule_id"])
    summaries = []
    for row in rows:
        original = row.get("original_harnessaudit_metrics") or {}
        summaries.append(
            {
                "trial_id": row["trial_id"],
                "condition": row["condition"],
                "tool_calls": original.get("n_tool_calls"),
                "communications": original.get("n_communications"),
                "violations_by_type": row.get("violations_by_type"),
                "completion_status": row.get("completion_evaluation_status"),
                "judge_failures": row.get("judge_observations"),
                "error": row.get("execution_error"),
            }
        )
    save(root / "action_and_error_summaries.json", {"trials": summaries})
    lines = [
        "# Fixed-control BACH pilot — " + status["mode"],
        "",
        f"Actual real targets completed: **{completed}/{intended}**. "
        f"Started: {status['actual_target_executions_started']}. Reserved: {len(rows)}.",
        "Task completion and valid research evidence are reported separately.",
        "",
        "| Condition | Attempts | Valid research | Failed/unknown | Forbidden rate "
        "| 95% interval | Mean TCR |",
        "|---|---:|---:|---:|---|---|---|",
    ]
    for c, s in stats["conditions"].items():
        lines.append(
            f"| {c} | {s['attempts']} | {s['valid_research_executions']} | "
            f"{s['invalid_failed_or_incomplete']} | {s['forbidden_trial_rate']} | "
            f"{s['wilson_95_interval']} | {s['mean_task_completion']} |"
        )
    lines += [
        "",
        stats["uncertainty_caveat"],
        "",
        "Preliminary sample differences:",
        "```json",
        json.dumps(stats["preliminary_excess_risk"], indent=2),
        "```",
        "",
        "Missing results and interrupted attempts are retained. No automatic replacement, "
        "adaptive selection or causal attribution is performed.",
        "",
    ]
    (root / "report.md").write_text("\n".join(lines))
    return status


def package_review(root, ledger):
    report(root, ledger)
    files = {}
    extensions = {".json", ".jsonl", ".csv", ".md", ".log", ".txt", ".prom"}
    for path in root.rglob("*"):
        rel = path.relative_to(root)
        if (
            not path.is_file()
            or path.is_symlink()
            or path.suffix not in extensions
            or not path.resolve().is_relative_to(root)
        ):
            continue
        if (
            path.name in {"settings.json", "mcp_config.json", "mcp.json"}
            or "mcp" in path.name.lower()
        ):
            continue
        if rel.parts[0] in {"source", ".control"} or "workspaces" in rel.parts:
            continue
        if rel.parts[0] == "executions" and "source" in rel.parts:
            continue
        if path.stat().st_size > 64 * 1024 * 1024:
            raise ValueError("Export log is too large to sanitize: " + str(rel))
        files[str(rel)] = path.read_text(errors="replace")
    sources = (
        {
            str(p.relative_to(root / "source")): p
            for p in (root / "source").rglob("*")
            if p.is_file()
        }
        if (root / "source").exists()
        else implementation_sources()
    )
    for name, path in sources.items():
        if (
            "fixtures/finance" not in name
        ):  # No extra datasets; original synthetic bank snapshots stay local.
            files["source/" + name] = path.read_text()
    for path in (REPO_ROOT / "tests").glob("test_*.py"):
        files["source/tests/" + path.name] = path.read_text()
    for name in ("pyproject.toml", "configs/local_qwen.yaml", "scripts/research_phase1/README.md"):
        files["source/" + name] = (REPO_ROOT / name).read_text()
    validator = Path(__file__).with_name("review_bundle.py")
    files["validate_bundle.py"] = validator.read_text()
    files["README.md"] = (
        "# BACH Phase 1 reviewer bundle\n\n"
        "Read run_status.json first: completed real targets, pending/invalid "
        "attempts, budgets and mode.\n"
        "MOCK is never real evidence. Execution completion is not legitimate task completion.\n"
        "Read report.md/report.json, datasets/trials.json, "
        "datasets/rule_observations.json and CSVs.\n"
        "Each executions/<slot>/ directory contains original per-trial evidence and failed logs.\n"
        "The smoke is B0-0 and is included in the 5/5/5 budget. No failed slot is "
        "silently replaced.\n"
        "gpu_metrics.jsonl, model_requests.json and server logs document "
        "placement/load/request attempts.\n"
        "Run: python validate_bundle.py .  (CPU-only; Python >=3.9; stdlib only).\n"
        "This verifies sanitized export hashes, rule totals, original deterministic "
        "tool/resource/channel\n"
        "matches, and recomputes descriptive Wilson/Newcombe intervals. Disclosure "
        "recognizer results\n"
        "and realized effects are not independently certified.\n"
        "The frozen task/notes and source are included. No weights, credentials or "
        "bank databases.\n"
        "Follow source/scripts/research_phase1/README.md to reproduce on authorized local GPUs.\n"
        "The tests/*.log files state what ran; unavailable checks are explicitly marked.\n"
        "Source/trial paths and credential values are sanitized copies; original "
        "local evidence is untouched.\n"
        "Original artifact hashes refer to original bytes; EXPORT_MANIFEST hashes "
        "refer to exported bytes.\n"
        "Tiny, concurrent, mixed-fixture trials support feasibility and descriptive "
        "comparisons only.\n"
    )
    credentials = [
        v
        for k, v in os.environ.items()
        if any(
            t in k.upper() for t in ("API_KEY", "AUTH_TOKEN", "ACCESS_TOKEN", "PASSWORD", "SECRET")
        )
        and len(v) >= 8
    ]
    exported = {}
    for name, text in files.items():
        text = text.replace(socket.gethostname(), "<HOST>")
        for secret in credentials:
            text = text.replace(secret, "<REDACTED_CREDENTIAL>")
        if name.endswith(".jsonl"):
            lines = []
            for line in text.splitlines():
                cleaned = sanitize(line, root).rstrip()
                try:
                    cleaned = json.dumps(
                        json.loads(cleaned), ensure_ascii=False, separators=(",", ":")
                    )
                except ValueError:
                    pass  # Retain malformed evidence; do not fabricate valid events.
                lines.append(cleaned)
            text = "\n".join(lines) + "\n"
        else:
            text = sanitize(text, root)
        exported[name] = text.encode()
    exported["EXPORT_MANIFEST.json"] = json.dumps(
        {
            "sanitized_copy": True,
            "sha256": {k: hashlib.sha256(v).hexdigest() for k, v in exported.items()},
            "original_evidence_modified": False,
        },
        indent=2,
    ).encode()
    # Validate the exact sanitized files before publishing the archive. No
    # inference, repository installation or GPU is needed for this recomputation.
    with tempfile.TemporaryDirectory(prefix="bach-review-validation-") as directory:
        staging = Path(directory)
        for name, data in exported.items():
            destination = staging / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
        validation = validate(staging)
    receipt = {k: validation[k] for k in ("validation", "trial_rows", "rule_rows", "limits")}
    exported["tests/bundle_validation.json"] = json.dumps(receipt, indent=2).encode()
    exported["EXPORT_MANIFEST.json"] = json.dumps(
        {
            "sanitized_copy": True,
            "original_evidence_modified": False,
            "sha256": {
                k: hashlib.sha256(v).hexdigest()
                for k, v in exported.items()
                if k != "EXPORT_MANIFEST.json"
            },
        },
        indent=2,
    ).encode()
    archive = root / "reviewer_bundle.tar.gz"
    temporary = root / "reviewer_bundle.tar.gz.tmp"
    with tarfile.open(temporary, "w:gz") as tar:
        for name, data in sorted(exported.items()):
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), 0o600
            tar.addfile(info, io.BytesIO(data))
    temporary.replace(archive)
    return archive
