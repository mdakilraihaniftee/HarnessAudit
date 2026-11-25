"""Read-only reconstruction of task-board polling and assignment evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .audit import contract_ids, domain_tool_names, native_calls, read, table, write
from .diagnostic import session_limit_evidence


def result_value(text):
    # Qwen sometimes repeats the rendered result after the MCP JSON envelope.
    try:
        value, _ = json.JSONDecoder().raw_decode(text.lstrip())
    except (ValueError, TypeError):
        return None
    return value.get("result", value) if isinstance(value, dict) else value


def task_board_review(folder):
    from multi_agent.schemas.task import TaskConfig

    folder = Path(folder)
    active = TaskConfig.model_validate(read(folder / "effective_task.json"))
    ids = contract_ids(active)
    calls = []
    sources = {}
    for path in sorted(folder.glob("native/*/*/trace.jsonl")):
        sources[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        calls.extend(dict(c, role=path.parent.name) for c in native_calls(path))
    calls.sort(key=lambda c: c.get("timestamp") or 0)
    polls, board = [], []
    for c in calls:
        name = c["tool_name"].rsplit("__", 1)[-1]
        value = result_value(c["result"])
        if c["role"] == "relationship_manager" and name == "task_list":
            polls.append(
                dict(
                    index=len(polls) + 1,
                    call_id=c["tool_call_id"],
                    timestamp=c["timestamp"],
                    result_timestamp=c["result_timestamp"],
                    arguments=c["arguments"],
                    actual_result=c["result"],
                    parsed_result=value,
                    is_error=c["is_error"],
                    registered=c["registered"],
                    source=c["source_trace_reference"],
                    call_line=c["source_call_line"],
                    result_line=c["source_result_line"],
                )
            )
        if name in {"task_list", "task_get", "task_update"} and not c["is_error"]:
            items = value if isinstance(value, list) else [value]
            for item in items:
                if isinstance(item, dict) and "id" in item and "status" in item:
                    board.append(
                        dict(
                            timestamp=c["result_timestamp"],
                            role=c["role"],
                            tool=name,
                            task_id=item["id"],
                            owner=item.get("owner"),
                            status=item["status"],
                            source=c["source_trace_reference"],
                        )
                    )
    board.sort(key=lambda r: r["timestamp"] or 0)
    previous = {}
    for row in board:
        old = previous.get(row["task_id"])
        row["previous_observed_status"] = old
        row["observed_transition"] = old is not None and old != row["status"]
        previous[row["task_id"]] = row["status"]
    trader = [c for c in calls if c["role"] == "trader"]
    assignments = []
    for c in trader:
        if c["is_error"] or c["tool_name"].rsplit("__", 1)[-1] not in {"task_list", "task_get"}:
            continue
        value = result_value(c["result"])
        for item in value if isinstance(value, list) else [value]:
            if isinstance(item, dict) and item.get("owner") == "trader":
                assignments.append(
                    dict(
                        task_id=item.get("id"),
                        status=item.get("status"),
                        contract_ids_present=all(
                            v in item.get("description", "") for v in ids.values()
                        ),
                        actual_description=item.get("description"),
                        source=c["source_trace_reference"],
                    )
                )
    finance = [
        c for c in trader if c["tool_name"].rsplit("__", 1)[-1] in domain_tool_names(active)
    ]
    return dict(
        source_sha256=sources,
        hub_task_list_calls=polls,
        task_observations=board,
        hub_poll_count=len(polls),
        identical_poll_arguments=len({json.dumps(p["arguments"], sort_keys=True) for p in polls})
        == 1,
        all_polls_returned_empty=bool(polls)
        and all(p["parsed_result"] == [] and not p["is_error"] for p in polls),
        observed_board_transitions=sum(r["observed_transition"] for r in board),
        trader_assignments=assignments,
        trader_finance_attempts=len(finance),
        hub_mailbox_sends=sum(
            c["role"] == "relationship_manager" and c["tool_name"].endswith("__mailbox_send")
            for c in calls
        ),
        trader_mailbox_receipts=[
            dict(result=c["result"], source=c["source_trace_reference"])
            for c in trader
            if c["tool_name"].endswith("__mailbox_receive")
        ],
        native_session_limits=session_limit_evidence(folder),
        limitation=(
            "Task observations reconstruct returned state only; "
            "no complete historical board snapshot between every poll."
        ),
    )


def review_smoke(campaign_dir, output_dir):
    root, output = Path(campaign_dir), Path(output_dir)
    report = dict(mode="CPU_READ_ONLY", campaign=str(root), trials={})
    for folder in sorted((root / "trials").iterdir()):
        if not (folder / "effective_task.json").exists():
            continue
        result = task_board_review(folder)
        report["trials"][folder.name] = result
        table(output / folder.name, "hub_task_list_calls", result["hub_task_list_calls"])
        table(output / folder.name, "task_observations", result["task_observations"])
    write(output / "review.json", report)
    return report
