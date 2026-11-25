"""Read-only reconstruction of scope and native-completion failures."""

from collections import Counter
from pathlib import Path

from .audit import native_calls, read, records


def review_trial(folder):
    folder = Path(folder)
    roles = {}
    for path in sorted((folder / "native").glob("*/*/trace.jsonl")):
        calls = native_calls(path)
        terminal = [
            dict(source_line=line, event=event)
            for line, event in records(path)
            if event.get("type") == "result"
        ]
        roles[path.parent.name] = dict(
            trace=str(path),
            tool_counts=dict(Counter(c["tool_name"] for c in calls)),
            missing_goal_errors=[
                c
                for c in calls
                if c["tool_name"] == "update_goal" and "No active Goal" in c["result"]
            ],
            terminal=terminal,
        )
    ledger = read(folder / "instrumentation/effects/effect_ledger.json", [])
    return dict(
        roles=roles,
        risk_updates=[
            {
                k: row[k]
                for k in [
                    "acting_agent",
                    "call_id",
                    "arguments",
                    "response_text",
                    "response_class",
                    "role_authorized",
                    "snapshot_evidence",
                ]
            }
            for row in ledger
            if row["tool_name"] == "set_risk_limits"
        ],
        convergence=read(folder / "instrumentation/runtime_convergence.json"),
        completion=read(folder / "instrumentation/effects/deterministic_completion.json"),
        interpretation=(
            "Trace evidence of scope violations and repeated missing-Goal errors; "
            "does not establish that a prompt repair will fix model behavior. "
            "API max_tokens=0 is recorded without rewriting requests or raising limits."
        ),
    )
