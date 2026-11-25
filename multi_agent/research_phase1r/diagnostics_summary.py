"""CPU-only arm imbalance and comparable benign concurrency summaries."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .audit import read, records, table, write


def invalidity_comparisons(arms):
    result = []
    for high in arms:
        for low in arms:
            if high["condition"] == low["condition"]:
                continue
            hn, ln = high["denominator"], low["denominator"]
            hr = high["measurement_invalid"] / hn if hn else None
            lr = low["measurement_invalid"] / ln if ln else None
            ratio = hr / lr if hr is not None and lr else None
            result.append(
                dict(
                    arm=high["condition"],
                    comparator=low["condition"],
                    numerator_invalid=high["measurement_invalid"],
                    denominator_started=hn,
                    comparator_invalid=low["measurement_invalid"],
                    comparator_started=ln,
                    observed_rate=hr,
                    comparator_rate=lr,
                    observed_ratio=ratio,
                    more_than_twice=ratio > 2 if ratio is not None else None,
                    status="NOT_EVALUATED"
                    if hr is None or lr is None
                    else "ZERO_COMPARATOR"
                    if lr == 0
                    else "OBSERVED_DESCRIPTIVE",
                    unknown_counts=[high["measurement_unknown"], low["measurement_unknown"]],
                    caution=(
                        "Small samples and unknowns limit interpretation. "
                        "Zero comparator does not establish resistance."
                    ),
                )
            )
    return result


def benchmark_comparison(root):
    root = Path(root)
    rows = []
    for concurrency in [2, 4]:
        folder = root / "concurrency_benchmark" / str(concurrency)
        campaign = read(folder / "campaign.json", {})
        report = read(folder / "diagnostic_report.json", {})
        if not campaign or not report:
            continue
        arms = report.get("conditions", [])
        n = sum(a["denominator"] for a in arms)
        invalid = sum(a["measurement_invalid"] for a in arms)
        unknown = sum(a["measurement_unknown"] for a in arms)
        valid = sum(a["measurement_valid"] for a in arms)
        cleanup = campaign.get("cleanup", {})
        clean = cleanup.get("ok") is True and (
            cleanup.get("reused_or_not_started") is True
            or cleanup.get("gpu_resources_released") is True
        )
        clean = clean and all(
            a.get("cleanup", {}).get("ok") is True for a in campaign.get("attempts", [])
        )
        elapsed = campaign.get("finished_at", 0) - campaign.get("started_at", 0)
        gpu_seconds = elapsed * len(campaign.get("gpu_ids", []))
        memory = {}
        for _, sample in records(folder / "gpu_metrics.jsonl"):
            for device in sample.get("devices", []):
                used = device.get("used_gb")
                if used is not None:
                    key = str(device["index"])
                    memory[key] = max(memory.get(key, 0), used)
        workload = [
            {key: r.get(key) for key in ["trial_id", "condition", "trial_seed", "note"]}
            for r in campaign.get("schedule", [])
        ]
        workload.sort(key=lambda r: r["trial_id"])
        workload_hash = hashlib.sha256(json.dumps(workload, sort_keys=True).encode()).hexdigest()
        rows.append(
            dict(
                concurrency=concurrency,
                target_identity=campaign.get("target_identity"),
                workload_sha256=workload_hash,
                started_trials=n,
                measurement_invalid=invalid,
                measurement_unknown=unknown,
                invalid_rate=invalid / n if n else None,
                unknown_rate=unknown / n if n else None,
                valid_trials_per_hour=report.get("valid_trials_per_hour"),
                valid_trials_per_gpu_hour=3600 * valid / gpu_seconds if gpu_seconds > 0 else None,
                gpu_seconds_upper_cost_proxy=gpu_seconds,
                model_requests_per_second=report.get("model_requests_per_second"),
                mean_target_runtime_seconds=report.get("mean_target_runtime_seconds"),
                native_tool_error_count=report.get("tool_error_frequency"),
                peak_used_gib_by_gpu=memory,
                cleanup_ok=clean,
                budget_censored_trials=report.get("budget_censored_trials", []),
                reliability_eligible=bool(
                    n
                    and (invalid + unknown) / n <= 0.1
                    and clean
                    and not report.get("budget_censored_trials")
                ),
                source_artifacts=[
                    str(folder / "campaign.json"),
                    str(folder / "diagnostic_report.json"),
                    str(folder / "gpu_metrics.jsonl"),
                ],
            )
        )
    comparable = (
        len(rows) == 2
        and len({r["target_identity"] for r in rows}) == 1
        and len({r["workload_sha256"] for r in rows}) == 1
    )
    eligible = [
        r for r in rows if r["reliability_eligible"] and r["valid_trials_per_gpu_hour"] is not None
    ]
    best = (
        max(eligible, key=lambda r: r["valid_trials_per_gpu_hour"])
        if eligible and comparable
        else None
    )
    summary = dict(
        status="OBSERVED_DESCRIPTIVE"
        if comparable
        else "NOT_EVALUATED"
        if not rows
        else "NOT_COMPARABLE",
        comparable_target_and_workload=comparable,
        provisional_engineering_preference=best["concurrency"] if best else None,
        basis=(
            "Reliably observed trials per GPU-hour subject to invalidity, "
            "censoring and cleanup, not GPU utilization."
        ),
        caveat=(
            "Small diagnostic samples do not establish a statistically "
            "confirmed throughput advantage."
        ),
        rows=rows,
    )
    table(root, "concurrency_comparison", rows)
    write(root / "concurrency_comparison_summary.json", summary)
    return summary
