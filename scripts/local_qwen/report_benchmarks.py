#!/usr/bin/env python3
"""Merge benchmark.json files under a directory into a markdown table + CSV."""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
rows = []
for path in sorted(root.rglob("benchmark.json")):
    data = json.loads(path.read_text())
    servers = data["meta"].get("servers") or []
    for r in data["results"]:
        gpu = r.get("gpu") or {}
        rows.append({
            "config": r["label"], "concurrency": r["concurrency"], "requests": r["requests"],
            "ok": r["ok"], "p50_latency_s": r["latency_p50_s"],
            "p95_latency_s": r["latency_p95_s"],
            "ttft_p50_s": r["ttft_p50_s"], "tok_s_per_request": r["per_request_output_tok_s"],
            "aggregate_tok_s": r["aggregate_output_tok_s"], "req_s": r["requests_per_s"],
            "gpus": ",".join(gpu) or "-",
            "peak_mem_gb": ",".join(str(v["peak_used_gb"]) for v in gpu.values()) or "-",
            "mean_util_pct": ",".join(str(v["mean_util_pct"]) for v in gpu.values()) or "-",
            "tp": ",".join(str(s.get("tensor_parallel_size")) for s in servers) or "-",
            "replicas": ",".join(str(s.get("replicas")) for s in servers) or "-",
        })
skipped = [p.stem.replace(".skipped", "") for p in root.glob("*.skipped.json")]
if rows:
    with (root / "summary.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    cols = list(rows[0])
    print("| " + " | ".join(cols) + " |")
    print("|" + "---|" * len(cols))
    for r in rows:
        print("| " + " | ".join(str(r[c]) for c in cols) + " |")
if skipped:
    print(f"\nSKIPPED configurations: {', '.join(skipped)}")
