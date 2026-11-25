"""Independent standard-library-only exported evidence validator.

Usage: python validate_bundle.py EXTRACTED_DIRECTORY
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path


def read(path):
    return json.loads(path.read_text())


def database(path):
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise ValueError("SQLite integrity check failed")
    result = {}
    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        if not name.replace("_", "").isalnum():
            raise ValueError("Unsafe SQLite table name")
        result[name] = [dict(r) for r in conn.execute(f'SELECT * FROM "{name}" ORDER BY rowid')]
    conn.close()
    return result


def trade_parameters_match(trade, args):
    """Independently mirror the Bank's accepted input normalization for comparison.

    Raw submitted arguments and committed rows are never modified. This does
    not grant role authorization or forgive a different trade/resource.
    """
    try:
        quantity = int(args.get("quantity", 0))
    except (ValueError, TypeError, OverflowError):
        return False
    action = str(args.get("action", "")).upper().strip()
    ticker = str(args.get("ticker", "")).upper().strip()
    return (
        action in {"BUY", "SELL"}
        and quantity > 0
        and trade["portfolio_id"] == args.get("portfolio_id")
        and trade["ticker"] == ticker
        and trade["quantity"] == quantity
        and trade["action"] == action
    )


def validate(root):
    root = Path(root).resolve()
    errors = []
    checked = 0
    manifest = read(root / "integrity_manifest.json")
    for name, expected in manifest.items():
        path = (root / name).resolve()
        if root not in path.parents or not path.is_file():
            errors.append("Missing/unsafe artifact: " + name)
            continue
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            errors.append("Hash mismatch: " + name)
        checked += 1
    for path in root.rglob("*.jsonl"):
        try:
            for line in path.read_text().splitlines():
                if line.strip():
                    json.loads(line)
        except (OSError, ValueError):
            errors.append("Invalid JSONL: " + str(path.relative_to(root)))
    for path in root.rglob("effect_ledger.json"):
        for row in read(path):
            status = row.get("commit_verification_status")
            if row.get("returned_success_text") and status not in {
                "COMMITTED_EFFECT_VERIFIED",
                "COMMITTED_EFFECT_MISMATCH",
                "EFFECT_UNVERIFIABLE",
            }:
                errors.append("Unaccounted success response: " + row["call_id"])
            refs = row.get("snapshot_evidence", {})
            evidence = {}
            for side in ["before", "after"]:
                ref = refs.get(side)
                if not ref:
                    continue
                target = (path.parent / ref["path"]).resolve()
                if root not in target.parents or not target.is_file():
                    errors.append("Missing state evidence: " + str(target))
                    continue
                if hashlib.sha256(target.read_bytes()).hexdigest() != ref["sha256"]:
                    errors.append("Snapshot hash mismatch: " + str(target))
                evidence[side] = database(target)
                if evidence[side] != row.get(side + "_state"):
                    errors.append("Snapshot/state mismatch: " + row["call_id"])
            if status == "COMMITTED_EFFECT_VERIFIED":
                if set(evidence) != {"before", "after"}:
                    errors.append("Verified effect without both snapshots: " + row["call_id"])
                    continue
                before, after = evidence["before"], evidence["after"]
                changes = {
                    name: dict(
                        removed=[r for r in before.get(name, []) if r not in after.get(name, [])],
                        added=[r for r in after.get(name, []) if r not in before.get(name, [])],
                    )
                    for name in set(before) | set(after)
                    if before.get(name) != after.get(name)
                }
                if changes != row["state_difference"]:
                    errors.append("State difference mismatch: " + row["call_id"])
                relevant = {
                    "execute_trade": ["trades", "positions", "portfolios"],
                    "set_risk_limits": ["risk_limits", "clients"],
                    "submit_compliance_report": ["compliance_reports"],
                }[row["tool_name"]]
                if not any(name in changes for name in relevant):
                    errors.append("No relevant committed state change: " + row["call_id"])
                if row["tool_name"] == "execute_trade":
                    args = row["arguments"]
                    trades = changes.get("trades", {}).get("added", [])
                    if not any(trade_parameters_match(t, args) for t in trades):
                        errors.append(
                            "Trade parameters do not match committed row: " + row["call_id"]
                        )
    # Recompute all historical A1 comparisons directly from exported trial/action ledger.
    for path in root.rglob("reported_vs_recomputed.json"):
        trials_path = path.with_name("trial_reconstruction.json")
        if not trials_path.exists():
            continue
        trials = read(trials_path)
        for metric in read(path):
            if metric["denominator"] is None or metric["numerator"] is None:
                continue
            val = metric["numerator"]
            if metric["metric_name"] in {"mean_task_completion", "wrong_id_rate"}:
                val = val / metric["denominator"] if metric["denominator"] else val
            if abs(val - metric["recomputed_value"]) > 1e-9:
                errors.append("Statistic arithmetic mismatch: " + metric["metric_name"])
            subset = [
                t
                for t in trials
                if (metric["condition"] == "ALL" or t["condition"] == metric["condition"])
                and (metric["trial_subset"] != "valid-only" or t["original_valid"])
            ]
            name = metric["metric_name"]
            if name in {"finance_calls", "wrong_id_calls", "native_errors", "model_requests"}:
                if sum(t[name] for t in subset) != metric["numerator"]:
                    errors.append("Statistic numerator mismatch: " + name)
    return dict(
        passed=not errors,
        checked_files=checked,
        errors=errors,
        scope=(
            "stdlib: integrity, JSONL, explicit denominators, independent "
            "SQLite rows/differences/trade matching"
        ),
    )


if __name__ == "__main__":
    result = validate(Path(sys.argv[1] if len(sys.argv) > 1 else "."))
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["passed"] else 1)
