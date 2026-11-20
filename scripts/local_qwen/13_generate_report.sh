#!/usr/bin/env bash
# 13_generate_report.sh — collect every local-Qwen artifact under results/local_qwen
# into one report directory: clean-run summaries, campaign reports, benchmarks,
# health/tool tests, cost audits, servers and provenance.
# Usage: scripts/local_qwen/13_generate_report.sh  [RESULTS_ROOT]
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
ha_require_python
ROOT="${1:-$HA_RUN_ROOT}"
OUT="$(ha_new_run_dir report)"
"$HA_PYTHON" - "$ROOT" "$OUT" <<'PY'
import json, sys
from pathlib import Path
root, out = Path(sys.argv[1]), Path(sys.argv[2])
lines = ["# Local Qwen results report", ""]
def section(title): lines.extend(["", f"## {title}", ""])
section("Cost audits (verify-cost)")
for p in sorted(root.glob("**/verify_cost.json")):
    d = json.loads(p.read_text())
    lines.append(f"- `{p.relative_to(root)}`: **{d['verdict']}** (charges possible: "
                 f"{d['can_incur_model_api_charges']})")
section("Health and tool-calling tests")
for p in sorted(list(root.glob("**/health.json")) + list(root.glob("**/test_tools.json"))):
    for r in json.loads(p.read_text()):
        lines.append(f"- {r['status']} `{r['name']}` ({p.parent.name}): {r['detail'][:140]}")
section("Clean / integration runs")
for p in sorted(root.glob("**/summary.json")):
    d = json.loads(p.read_text())
    for r in d.get("runs", []):
        lines.append(f"- `{r['run_id']}` tcr={r['completion_tcr']} unique_violations="
                     f"{r['unique_violations']} tool_calls={r['tool_calls']} comms="
                     f"{r['communications']} agent_time={r['agent_wall_time_s']}s "
                     f"requests={r['model_requests']} error={r['error']}")
    if d.get("gpu_peaks"):
        lines.append(f"  - GPU peaks ({p.parent.name}): {d['gpu_peaks']}")
for p in sorted(root.glob("**/integration_verdict.json")):
    d = json.loads(p.read_text())
    lines.append(f"- integration verdict `{d['run_id']}`: all_pass={d['all_pass']}")
    for k, v in d["checks"].items():
        lines.append(f"  - {'PASS' if v['pass'] else 'FAIL'} {k}: {v['detail'][:150]}")
section("SI-ATG campaigns")
for p in sorted(root.glob("**/campaign_*/report.json")):
    d = json.loads(p.read_text())
    cfg = d.get("experiment_config") or {}
    lines.append(f"- campaign `{d['campaign_id']}` backend={cfg.get('backend')} "
                 f"target={cfg.get('target_harness')}/{cfg.get('target_model')}")
    for a in d["attempts"]:
        lines.append(f"  - round {a['round']} strategy={a['strategy']} violations="
                     f"{a['violations']} completion={a['task_completion']} reward={a['reward']}")
section("Serving benchmarks")
for p in sorted(root.glob("**/benchmark*/summary.md")):
    lines.append(f"### {p.parent.name}")
    lines.append(p.read_text())
(out / "REPORT.md").write_text("\n".join(lines) + "\n")
print("\n".join(lines))
PY
ha_info "report: $OUT/REPORT.md"
