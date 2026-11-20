#!/usr/bin/env python3
"""Summarize HarnessAudit result JSONs (one row per run) into JSON + CSV.

Usage: summarize_runs.py RESULTS_DIR OUT_PREFIX [--gpu-samples gpu.csv] [--times times.tsv]
Unique violations are recomputed from the normalized trace with the same logic
the SI-ATG gateway uses (multi_agent.attacks.gateway.inspect_trace_file).
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TASK_ROOTS = (REPO / "multi_agent" / "tasks", REPO / "tests" / "fixtures" / "synthetic")
sys.path.insert(0, str(REPO))


def gpu_peaks(path: Path | None) -> dict[str, dict[str, float]]:
    peaks: dict[str, dict[str, float]] = {}
    if not path or not path.exists():
        return peaks
    for row in csv.reader(path.read_text().splitlines()):
        if len(row) < 4 or not row[1].strip().isdigit():
            continue
        idx, mem, util = row[1].strip(), row[2].strip().split()[0], row[3].strip().split()[0]
        try:
            p = peaks.setdefault(idx, {"peak_mem_mib": 0.0, "max_util_pct": 0.0})
            p["peak_mem_mib"] = max(p["peak_mem_mib"], float(mem))
            p["max_util_pct"] = max(p["max_util_pct"], float(util))
        except ValueError:
            continue
    return peaks


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("results_dir")
    ap.add_argument("out_prefix")
    ap.add_argument("--gpu-samples")
    ap.add_argument("--times")
    a = ap.parse_args()
    from multi_agent.attacks.gateway import inspect_trace_file
    from multi_agent.loader import load_task_with_tools

    times = {}
    if a.times and Path(a.times).exists():
        for line in Path(a.times).read_text().splitlines():
            parts = line.split("\t")
            if len(parts) >= 3:
                times[parts[0]] = {"wall_s": float(parts[1]), "exit_code": int(parts[2])}
    rows = []
    for res in sorted(Path(a.results_dir).rglob("*.json")):
        try:
            rep = json.loads(res.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if "run_id" not in rep or "trace_file" not in rep:
            continue
        task_yaml = next(
            p for root in TASK_ROOTS
            for p in root.rglob(f"{rep['task_id']}.yaml"))
        task, _ = load_task_with_tools(task_yaml)
        try:
            unique = len(inspect_trace_file(rep["trace_file"], task.access_rules).violations)
        except Exception as exc:  # noqa: BLE001
            unique = f"error: {exc}"
        prov = rep.get("inference_provenance") or {}
        native = (prov.get("native_summary") or {}).get("agents", {})
        requests = sum(sum((v.get("model_requests") or {}).values()) for v in native.values())
        non_loopback = sorted({x for v in native.values()
                               for x in (v.get("egress") or {}).get("non_loopback", [])})
        rows.append({
            "run_id": rep["run_id"], "task_id": rep["task_id"], "model": rep.get("llm"),
            "harness": prov.get("harness"), "inference_mode": prov.get("inference_mode"),
            "completion_tcr": rep.get("tcr"),
            "deterministic_completion": rep.get("deterministic_completion_score"),
            "llm_completion": rep.get("llm_completion_score"),
            "judge_model": prov.get("judge_model"),
            "unique_violations": unique, "raw_violations": rep.get("n_violations"),
            "violations_by_code": rep.get("violations_by_code"),
            "tool_calls": rep.get("n_tool_calls"),
            "communications": rep.get("n_communications"),
            "actions": rep.get("n_actions"), "agent_wall_time_s": rep.get("wall_time_s"),
            "total_wall_time_s": times.get(rep["run_id"], {}).get("wall_s"),
            "model_requests": requests or None,
            "non_loopback_egress": non_loopback,
            "error": rep.get("error"), "result_json": str(res),
        })
    samples = Path(a.gpu_samples) if a.gpu_samples else None
    summary = {"runs": rows, "gpu_peaks": gpu_peaks(samples)}
    Path(f"{a.out_prefix}.json").write_text(json.dumps(summary, indent=2, default=str))
    if rows:
        with open(f"{a.out_prefix}.csv", "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            for r in rows:
                w.writerow({k: json.dumps(v) if isinstance(v, (dict, list)) else v
                            for k, v in r.items()})
    for r in rows:
        print(f"{r['run_id']}: tcr={r['completion_tcr']} "
              f"unique_violations={r['unique_violations']} "
              f"tool_calls={r['tool_calls']} comms={r['communications']} "
              f"agent_time={r['agent_wall_time_s']}s requests={r['model_requests']} "
              f"egress_non_loopback={r['non_loopback_egress'] or 'none'} error={r['error']}")
    print(f"GPU peaks: {summary['gpu_peaks']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
