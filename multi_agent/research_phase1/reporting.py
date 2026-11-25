"""Conservative summaries and sanitized, allowlisted review archives."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import tarfile
from pathlib import Path

from .experiment import read_json, save


def csv_write(path, rows, fallback):
    keys = list(dict.fromkeys(k for row in rows for k in row)) or fallback
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        writer.writerows(
            {
                k: json.dumps(v, sort_keys=True) if isinstance(v, (dict, list)) else v
                for k, v in row.items()
            }
            for row in rows
        )


def analyze(root):
    root = Path(root).resolve()
    manifest = read_json(root / "run_manifest.json")
    trials, observations, errors = [], [], []
    seen = set()
    for attempt in manifest["attempts"]:
        tid = attempt["trial_id"]
        if tid in seen:
            raise ValueError("Duplicate manifest trial ID")
        seen.add(tid)
        folder = root / "trials" / tid
        try:
            integrity = folder / "artifact_manifest.json"
            if (
                attempt.get("artifact_manifest_sha256")
                != hashlib.sha256(integrity.read_bytes()).hexdigest()
            ):
                raise ValueError("Artifact manifest checksum mismatch or interrupted attempt")
            for rel, checksum in read_json(integrity)["sha256"].items():
                artifact = folder / rel
                if not artifact.resolve().is_relative_to(folder) or artifact.is_symlink():
                    raise ValueError("Artifact path escapes trial directory")
                if hashlib.sha256(artifact.read_bytes()).hexdigest() != checksum:
                    raise ValueError(f"Artifact checksum mismatch: {rel}")
            row = read_json(folder / "trial.json")
            if row.get("trial_id") != tid or row.get("experiment_id") != manifest["experiment_id"]:
                raise ValueError("Trial identity mismatch")
            if row["execution_status"] == "success":
                # A corrupt result must not remain a successful zero-violation trial.
                result = read_json(folder / Path(row["result_json_path"]).relative_to(folder))
                if result.get("run_id") != row.get("target_run_id") or "n_actions" not in result:
                    raise ValueError("Malformed evaluator result identity/schema")
                from multi_agent.schemas.trace import ExecutionTrace

                trace = ExecutionTrace.model_validate(read_json(folder / "execution_trace.json"))
                if trace.run_id != row["target_run_id"] or trace.n_actions != len(trace.actions):
                    raise ValueError("Invalid saved execution trace")
            obs = read_json(folder / "rule_observations.json")["observations"]
            if not isinstance(obs, list) or any(o.get("trial_id") != tid for o in obs):
                raise ValueError("Invalid rule observations")
            observations.extend(obs)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            errors.append({"trial_id": tid, "error": str(exc)})
            row = {
                "trial_id": tid,
                "experiment_id": manifest["experiment_id"],
                "condition": attempt["condition"],
                "execution_status": "failed_or_interrupted",
                "execution_error": str(exc),
                "mode": manifest["configuration"]["mode"],
            }
            # Retain the missing trial's rule rows as unknown, rather than dropping it.
            rules = read_json(root / "rule_catalog.json")["rules"]
            observations.extend(
                {
                    "trial_id": tid,
                    "rule_id": r["rule_id"],
                    "rule_type": r["rule_type"],
                    "agent_role": r["agent_role"],
                    "rule_applicable": None,
                    "violation_observed": None,
                    "attempt_observed": None,
                    "effect_observed": None,
                    "event_references": [],
                    "evidence_quality": "unknown_missing_or_invalid_artifact",
                }
                for r in rules
            )
        trials.append(row)
    summaries = []
    rates = {}
    for condition in manifest["configuration"]["conditions"]:
        rows = [r for r in trials if r["condition"] == condition]
        ok = [
            r
            for r in rows
            if r["execution_status"] == "success"
            and r.get("evidence_status") == "complete_observed_trace"
        ]
        measured = [r for r in ok if r.get("forbidden_violation_count") is not None]
        rate = (
            sum(r["forbidden_violation_count"] > 0 for r in measured) / len(measured)
            if measured
            else None
        )
        rates[condition] = rate

        def total(field):
            vals = [r[field] for r in ok if r.get(field) is not None]
            return sum(vals) if vals else None

        def by_kind(kind):
            return sum(r.get("violations_by_type", {}).get(kind, 0) for r in ok) if ok else None

        completion = [r["task_completion"] for r in ok if r.get("task_completion") is not None]
        deterministic = [
            r["deterministic_completion_score"]
            for r in ok
            if r.get("deterministic_completion_score") is not None
        ]
        summaries.append(
            {
                "condition": condition,
                "attempts": len(rows),
                "successful_executions": len(ok),
                "failed_executions": len(rows) - len(ok),
                "task_completion_mean": sum(completion) / len(completion) if completion else None,
                "completion_evaluated_n": len(completion),
                "deterministic_completion_mean": (
                    sum(deterministic) / len(deterministic) if deterministic else None
                ),
                "rate_denominator": len(measured),
                "forbidden_trial_rate": rate,
                "forbidden_rule_violations": total("forbidden_violation_count"),
                "unnecessary_tool_violations": total("unnecessary_tool_count"),
                "communication_violations": by_kind("forbidden_communication"),
                "resource_violations": by_kind("forbidden_resource"),
                "execution_time_seconds": sum(r.get("runtime_seconds") or 0 for r in rows),
                "failed_trial_ids": [r["trial_id"] for r in rows if r not in ok],
            }
        )

    def difference(a, b):
        return (
            rates[a] - rates[b] if rates.get(a) is not None and rates.get(b) is not None else None
        )

    report = {
        "experiment_id": manifest["experiment_id"],
        "mode": manifest["configuration"]["mode"],
        "conditions": summaries,
        "artifact_errors": errors,
        "failed_or_interrupted_executions": sum(s["failed_executions"] for s in summaries),
        "preliminary_sample_differences": {
            "B2_minus_B1": difference("B2", "B1"),
            "B1_minus_B0": difference("B1", "B0"),
        },
        "interpretation": "Descriptive only. Fixed notes, tiny samples; no causal significance or "
        "certified attack. Rates use complete successful observed trajectories; all failures "
        "are retained and shown separately. Null is unavailable, never zero. "
        "No realized-effect inference.",
        "resume_policy": "Existing slots are never rerun. For replacements use a new experiment "
        "directory and document the prior trial ID and reason; preserve both archives.",
    }
    save(root / "report.json", report)
    csv_write(root / "trial_summary.csv", trials, ["trial_id", "execution_status"])
    csv_write(root / "rule_observations.csv", observations, ["trial_id", "rule_id"])
    lines = [
        f"# BACH Phase 1 — {report['mode']}",
        "",
        report["interpretation"],
        "",
        "| Condition | Successful | Failed | TCR mean | Forbidden events | Unnecessary tools "
        "| Communication | Resource | Seconds | Forbidden trial rate (n) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]

    def display(value):
        return (
            "unavailable"
            if value is None
            else str(round(value, 4) if isinstance(value, float) else value)
        )

    for s in summaries:
        vals = [
            s[k]
            for k in (
                "condition",
                "successful_executions",
                "failed_executions",
                "task_completion_mean",
                "forbidden_rule_violations",
                "unnecessary_tool_violations",
                "communication_violations",
                "resource_violations",
                "execution_time_seconds",
            )
        ]
        lines.append(
            "| "
            + " | ".join(map(display, vals))
            + f" | {display(s['forbidden_trial_rate'])} ({s['rate_denominator']}) |"
        )
    lines.extend(
        [
            "",
            "Preliminary sample differences: "
            + json.dumps(report["preliminary_sample_differences"]),
            "",
            "Failed/interrupted trials: "
            + json.dumps([r["trial_id"] for r in trials if r["execution_status"] != "success"]),
            "",
            report["resume_policy"],
            "",
        ]
    )
    (root / "report.md").write_text("\n".join(lines))
    return report


SECRET_KEY = re.compile(r"(?i)(api[_-]?key|authorization|access[_-]?token|password|secret)")
TOKEN = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{16})\b")


def sanitize(text, root):
    from multi_agent.research_phase1.experiment import REPO_ROOT

    text = text.replace(str(root), "<EXPERIMENT_ROOT>").replace(str(REPO_ROOT), "<REPO_ROOT>")
    text = text.replace(str(Path.home()), "<USER_HOME>")
    text = TOKEN.sub("<REDACTED_CREDENTIAL>", text)
    text = re.sub(r"(?i)(bearer\s+)[^\s\"']+", r"\1<REDACTED>", text)
    text = re.sub(
        r"(?i)((?:api[_-]?key|password|secret|access[_-]?token)\s*[=:]\s*)[^\s,;\"']+",
        r"\1<REDACTED>",
        text,
    )

    def scrub(value):
        if isinstance(value, dict):
            return {
                k: "<REDACTED>" if SECRET_KEY.search(k) else scrub(v) for k, v in value.items()
            }
        if isinstance(value, list):
            return [scrub(v) for v in value]
        return value

    try:
        return json.dumps(scrub(json.loads(text)), indent=2) + "\n"
    except ValueError:
        return text


def package(root, archive=None):
    root = Path(root).resolve()
    analyze(root)
    archive = Path(archive or root.with_suffix(".tar.gz")).resolve()
    exported = {}
    root_files = {
        "effective_experiment_config.json",
        "requester_notes.json",
        "rule_catalog.json",
        "gpu_verification.json",
        "run_manifest.json",
        "report.json",
        "report.md",
        "trial_summary.csv",
        "rule_observations.csv",
        "replacement_reason.txt",
    }
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if (
            path.is_symlink()
            or not path.is_file()
            or path.suffix not in {".json", ".jsonl", ".csv", ".md", ".log", ".py", ".sh", ".yaml"}
        ):
            continue
        if len(rel.parts) == 1 and path.name not in root_files:
            continue
        if len(rel.parts) > 1 and rel.parts[0] not in {"trials", "source"}:
            continue
        if len(rel.parts) > 2 and rel.parts[0] == "trials":
            section = rel.parts[2]
            allowed_trial_files = {
                "trial.json",
                "execution.log",
                "execution_trace.json",
                "effective_task.json",
                "rule_observations.json",
                "inference_config.json",
                "artifact_manifest.json",
                "judge_observations.json",
            }
            if len(rel.parts) == 3 and path.name not in allowed_trial_files:
                continue
            if len(rel.parts) > 3 and section not in {"results", "traces", "native"}:
                continue
            if section == "native" and path.name not in {
                "trace.jsonl",
                "stderr.log",
                "netlog.jsonl",
                "usage_record.jsonl",
                "native_summary.json",
            }:
                continue
        if (
            path.name in {"settings.json", "mcp.json", "mcp_config.json"}
            or "mcp" in path.name.lower()
        ):
            continue
        if path.stat().st_size > 32 * 1024 * 1024:
            raise ValueError(f"Review file too large to sanitize: {rel}")
        exported[str(rel)] = sanitize(path.read_text(), root).encode()
    checksums = {name: hashlib.sha256(data).hexdigest() for name, data in exported.items()}
    exported["EXPORT_MANIFEST.json"] = json.dumps(
        {
            "sanitized_copy": True,
            "original_evidence_modified": False,
            "sha256": checksums,
            "exclusions": [
                "SQLite snapshots",
                "MCP configuration",
                "settings",
                "model weights",
                "symlinks",
            ],
            "note": "Hashes cover exported bytes. Original manifest remains provenance; "
            "local paths "
            "in the export are placeholders. Inspect exported logs before sharing.",
        },
        indent=2,
    ).encode()
    archive.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "w:gz") as tar:
        for name, data in exported.items():
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), 0o600
            tar.addfile(info, io.BytesIO(data))
    return archive
