"""Read-only coordination evidence: per-call ledgers, no-progress diagnostic, timelines.

Everything here is derived, CPU-only and observation-only. Inputs are a trial's raw
native traces plus already-written instrumentation; nothing in the trial folder is
modified, no model is called, and no generated call is rewritten or replayed.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from .audit import native_calls, records

HUB = "relationship_manager"
SPECIALISTS = ("risk_officer", "trader", "compliance_analyst")
READ_ONLY_BOARD_TOOLS = {
    "task_list",
    "task_get",
    "task_stats",
    "board_team",
    "board_overview",
    "team_get",
    "team_members_list",
    "team_list",
}
MAILBOX_READ = {"mailbox_receive", "mailbox_peek", "mailbox_peek_count"}
ENUM_ERROR = re.compile(r"'([^']*)' is not a valid (TaskStatus|TaskPriority|MessageType)")
# Thresholds for the *descriptive* loop label only. They never gate, stop or alter a run.
NO_PROGRESS_LOOP_MIN_TAIL = 20


def short(name):
    return name.rsplit("__", 1)[-1]


def native_dir(trial):
    dirs = sorted(Path(trial).glob("native/*"))
    if len(dirs) != 1:
        raise ValueError(f"Expected one native run directory in {trial}, found {len(dirs)}")
    return dirs[0]


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def error_message(text):
    found = re.findall(r'"text":\s*"Error executing tool [^:]+: ((?:[^"\\]|\\.)*)"', text)
    if found:
        return json.loads('"' + found[0] + '"')
    return text.strip()[:500] or None


def error_class(call):
    """Refined coordination error taxonomy. The shared audit.error_taxonomy is unchanged."""
    if not call.get("is_error"):
        return None
    text = call.get("result") or ""
    if call.get("registered") is False:
        return "UNREGISTERED_TOOL_NAME"
    if ENUM_ERROR.search(text):
        return "INVALID_ENUM_ARGUMENT"
    lower = text.lower()
    if "team" in lower and "not found" in lower:
        return "WRONG_TEAM_IDENTIFIER"
    if "no active goal" in lower:
        return "NO_ACTIVE_CLI_GOAL"
    if "validation error" in lower or "field required" in lower:
        return "SCHEMA_VALIDATION_ERROR"
    if any(x in lower for x in ("econnrefused", "connection refused", "transport closed")):
        return "TRANSPORT_FAILURE"
    return "OTHER_TOOL_ERROR"


def parse_result(text):
    """First JSON line of a tool result (Qwen Code appends a plain-text copy)."""
    if not text:
        return None
    try:
        return json.loads(text.split("\n", 1)[0])
    except ValueError:
        return None


def returned_tasks(call):
    if call.get("is_error"):
        return None
    payload = parse_result(call.get("result"))
    rows = payload.get("result") if isinstance(payload, dict) else None
    if isinstance(rows, list):
        return [r for r in rows if isinstance(r, dict)]
    if isinstance(rows, dict) and "id" in rows:
        return [rows]
    return None


def session_stop(trace_dir):
    trace, stderr = trace_dir / "trace.jsonl", trace_dir / "stderr.log"
    terminal = [e for _, e in records(trace) if e.get("type") == "result"]
    text = stderr.read_text(errors="replace") if stderr.exists() else ""
    if terminal:
        return dict(
            final_response_observed=True,
            stop_reason="NATIVE_RESULT:" + str(terminal[-1].get("subtype")),
            num_turns=terminal[-1].get("num_turns"),
        )
    if "Reached max session turns for this session." in text:
        return dict(final_response_observed=False, stop_reason="SESSION_TURN_LIMIT")
    return dict(final_response_observed=False, stop_reason="NO_NATIVE_TERMINAL_UNKNOWN_CAUSE")


def all_calls(trial):
    out = {}
    for path in sorted(native_dir(trial).glob("*/trace.jsonl")):
        out[path.parent.name] = native_calls(path)
    return out


def init_tools(trace):
    for _, event in records(trace):
        if event.get("subtype") == "init":
            return event.get("tools", [])
    return []


def board_observations(trial, calls_by_role):
    """Task states that any agent actually saw returned, with timestamps."""
    obs = []
    for event in json.loads((Path(trial) / "instrumentation/assignment_events.json").read_text()):
        if event.get("event") == "assignment_ready_before_spawn":
            obs.append(
                dict(
                    timestamp=event["timestamp"],
                    task_id=event["assignment_id"],
                    owner=event["acting_agent"],
                    status="pending",
                    source="instrumentation/assignment_events.json (seeded before spawn)",
                )
            )
    for role, calls in calls_by_role.items():
        for call in calls:
            if short(call["tool_name"]) not in {"task_list", "task_get", "task_update"}:
                continue
            for task in returned_tasks(call) or []:
                obs.append(
                    dict(
                        timestamp=call.get("result_timestamp") or call.get("timestamp"),
                        task_id=task.get("id"),
                        owner=task.get("owner"),
                        status=task.get("status"),
                        source=f"{role}:{call['source_trace_reference'].rsplit('/', 1)[-1]}",
                    )
                )
    return sorted(obs, key=lambda o: o["timestamp"] or 0)


def board_state_at(observations, timestamp):
    state = {}
    for o in observations:
        if o["timestamp"] is not None and timestamp is not None and o["timestamp"] <= timestamp:
            state[o["task_id"]] = dict(owner=o["owner"], status=o["status"])
    return state


def _cycle_period(seq):
    """Smallest p such that the last >=2p items repeat with period p."""
    n = len(seq)
    for p in range(1, n // 2 + 1):
        tail = seq[-2 * p :]
        if tail[:p] == tail[p:]:
            k = n - p
            while k > 0 and seq[k - 1] == seq[k - 1 + p]:
                k -= 1
            return dict(period=p, repeating_suffix_length=n - k, cycle=seq[-p:])
    return None


def role_ledger(trial, role, calls_by_role=None):
    """Per-call ledger for one role (section 7.1), with derived board observations."""
    trial = Path(trial)
    calls_by_role = calls_by_role or all_calls(trial)
    calls = calls_by_role.get(role, [])
    observations = board_observations(trial, calls_by_role)
    seen_results, rows, run = {}, [], 0
    for i, call in enumerate(calls, 1):
        tool = short(call["tool_name"])
        args = {k: v for k, v in call["arguments"].items() if k != "team_name"}
        key = json.dumps([tool, args], sort_keys=True)
        tasks = returned_tasks(call)
        state = board_state_at(observations, call.get("timestamp"))
        owner = args.get("owner") if tool == "task_list" else None
        status = args.get("status") if tool == "task_list" else None
        matching_owner = (
            sorted(t for t, s in state.items() if s["owner"] == owner)
            if tool == "task_list" and owner
            else None
        )
        result_norm = call.get("result") if not call.get("is_error") else "ERROR"
        # New information = a successful result this role has not received before.
        new_info = not call.get("is_error") and result_norm not in seen_results.values()
        communicated = tool in {"mailbox_send", "mailbox_broadcast"} and not call.get("is_error")
        run = 0 if (new_info or communicated) else run + 1
        seen_results[key] = result_norm
        rows.append(
            dict(
                call_sequence=i,
                timestamp=call.get("timestamp"),
                result_timestamp=call.get("result_timestamp"),
                tool_name=call["tool_name"],
                input_arguments=call["arguments"],
                owner_filter=owner,
                status_filter=status,
                tool_result=call.get("result"),
                tool_error=bool(call.get("is_error")),
                error_message=error_message(call.get("result") or "")
                if call.get("is_error")
                else None,
                error_class=error_class(call),
                returned_task_ids=[t.get("id") for t in tasks] if tasks is not None else None,
                board_state_observation=dict(
                    method=(
                        "latest state returned to any agent (or seeded before spawn) at or "
                        "before this call; not a complete board snapshot"
                    ),
                    known_tasks=state,
                    known_task_count=len(state),
                    tasks_owned_by_filter_value=matching_owner,
                ),
                empty_result=tasks == [],
                empty_while_board_known_nonempty=tasks == [] and len(state) > 0,
                owner_filter_is_comma_list=bool(owner and "," in owner),
                unfiltered_task_list=tool == "task_list"
                and not args.get("owner")
                and not args.get("status")
                and not args.get("priority"),
                new_information=bool(new_info),
                consecutive_no_progress_count=run,
                source_trace_line=call.get("source_call_line"),
                source_result_line=call.get("source_result_line"),
            )
        )
    return rows


def ledger_summary(trial, role, rows, calls_by_role=None):
    trial = Path(trial)
    calls_by_role = calls_by_role or all_calls(trial)
    trace_dir = native_dir(trial) / role
    tools_available = [short(t) for t in init_tools(trace_dir / "trace.jsonl")]
    task_lists = [r for r in rows if short(r["tool_name"]) == "task_list"]
    invalid_values = Counter()
    for r in rows:
        if r["error_class"] == "INVALID_ENUM_ARGUMENT":
            m = ENUM_ERROR.search(r["tool_result"] or "")
            invalid_values[f"{m.group(2)}:{m.group(1)}"] += 1
    used = Counter(short(r["tool_name"]) for r in rows)
    reports = sorted(
        (c.get("timestamp"), sender)
        for sender, calls in calls_by_role.items()
        for c in calls
        if sender != role
        and short(c["tool_name"]) == "mailbox_send"
        and not c.get("is_error")
        and c["arguments"].get("to") == role
    )
    first_report = reports[0][0] if reports else None
    missed = (
        sum(
            1
            for r in rows
            if r["timestamp"]
            and r["timestamp"] > first_report
            and short(r["tool_name"]) not in MAILBOX_READ
        )
        if first_report
        else 0
    )
    arg_sequence = [json.dumps(r["input_arguments"], sort_keys=True) for r in task_lists]
    return dict(
        role=role,
        source_trace=str((trace_dir / "trace.jsonl").relative_to(trial)),
        source_trace_sha256=sha256(trace_dir / "trace.jsonl"),
        tool_call_count=len(rows),
        calls_by_tool=dict(used),
        task_list_calls=len(task_lists),
        valid_tool_invocations=sum(not r["tool_error"] for r in rows),
        rejected_tool_invocations=sum(r["tool_error"] for r in rows),
        error_classes=dict(Counter(r["error_class"] for r in rows if r["error_class"])),
        invalid_enum_values=dict(invalid_values),
        distinct_invalid_status_values=sorted(
            {k.split(":", 1)[1] for k in invalid_values if k.startswith("TaskStatus:")}
        ),
        owner_filter_values=dict(Counter(r["owner_filter"] for r in task_lists)),
        comma_owner_filter_calls=sum(r["owner_filter_is_comma_list"] for r in task_lists),
        empty_responses=sum(r["empty_result"] for r in rows),
        empty_responses_while_board_known_nonempty=sum(
            r["empty_while_board_known_nonempty"] for r in rows
        ),
        unfiltered_task_list_attempted=any(r["unfiltered_task_list"] for r in task_lists),
        valid_unfiltered_task_list_calls=sum(
            r["unfiltered_task_list"] and not r["tool_error"] for r in task_lists
        ),
        read_only_board_tools_available=sorted(READ_ONLY_BOARD_TOOLS & set(tools_available)),
        read_only_board_tools_used=sorted(READ_ONLY_BOARD_TOOLS & set(used)),
        mailbox_send_count=used.get("mailbox_send", 0) + used.get("mailbox_broadcast", 0),
        mailbox_receive_count=used.get("mailbox_receive", 0),
        reports_sent_to_role=[dict(timestamp=t, sender=s) for t, s in reports],
        calls_after_first_report_without_reading_mailbox=missed,
        distinct_argument_sets=len(set(arg_sequence)),
        argument_cycle=_cycle_period(arg_sequence),
        **session_stop(trace_dir),
    )


def progress_diagnostic(trial, calls_by_role=None):
    """Observation-only no-progress diagnostic (section 12). Never gates or stops a run."""
    trial = Path(trial)
    calls_by_role = calls_by_role or all_calls(trial)
    roles = {}
    for role, calls in sorted(calls_by_role.items()):
        rows = role_ledger(trial, role, calls_by_role)
        seen, repeated, board_states, progress = {}, 0, set(), 0
        for r, c in zip(rows, calls):
            tool = short(r["tool_name"])
            key = json.dumps([tool, r["input_arguments"]], sort_keys=True)
            if tool in READ_ONLY_BOARD_TOOLS | MAILBOX_READ and seen.get(key) == r["tool_result"]:
                repeated += 1
            seen[key] = r["tool_result"]
            if tool in READ_ONLY_BOARD_TOOLS and not r["tool_error"]:
                board_states.add(r["tool_result"])
            if r["new_information"] or (
                not r["tool_error"]
                and tool in {"mailbox_send", "mailbox_broadcast", "task_update", "task_create"}
            ):
                progress += 1
            elif not r["tool_error"] and not tool.startswith(
                ("task_", "board_", "mailbox_", "team_")
            ):
                progress += 1  # successful domain/native operation
        stop = session_stop(native_dir(trial) / role)
        tail = rows[-1]["consecutive_no_progress_count"] if rows else 0
        sends = sum(
            short(r["tool_name"]) in {"mailbox_send", "mailbox_broadcast"} and not r["tool_error"]
            for r in rows
        )
        flags = dict(
            repeated_equivalent_queries=repeated > 0,
            repeated_empty_results=sum(r["empty_result"] for r in rows) > 1,
            repeated_invalid_enum_arguments=sum(
                r["error_class"] == "INVALID_ENUM_ARGUMENT" for r in rows
            )
            > 1,
            no_mailbox_sends=sends == 0,
            no_final_response=not stop["final_response_observed"],
            session_turn_exhaustion=stop["stop_reason"] == "SESSION_TURN_LIMIT",
        )
        if stop["final_response_observed"]:
            label = "TERMINATED_WITH_FINAL_RESPONSE"
        elif tail >= NO_PROGRESS_LOOP_MIN_TAIL:
            label = "NO_PROGRESS_LOOP"
        else:
            label = "INCOMPLETE_WITH_RECENT_PROGRESS"
        roles[role] = dict(
            repeated_query_count=repeated,
            invalid_argument_count=sum(
                r["error_class"] in {"INVALID_ENUM_ARGUMENT", "SCHEMA_VALIDATION_ERROR"}
                for r in rows
            ),
            distinct_board_states_seen=len(board_states),
            mailbox_send_count=sends,
            tool_call_count=len(rows),
            progress_event_count=progress,
            trailing_no_progress_calls=tail,
            final_response_observed=stop["final_response_observed"],
            stop_reason=stop["stop_reason"],
            pattern_flags=flags,
            descriptive_label=label,
        )
    return dict(
        mode="OBSERVATION_ONLY",
        effect_on_run="none: no call rewriting, injected actions, early stop or success marking",
        label_rule=(
            f"NO_PROGRESS_LOOP = no native final response and the last "
            f">= {NO_PROGRESS_LOOP_MIN_TAIL} calls brought no new information or communication"
        ),
        progress_definition=(
            "successful send/task update/create, successful domain operation, or a read "
            "whose result differs from every earlier result of that role"
        ),
        roles=roles,
    )


def specialist_timelines(trial, calls_by_role=None, attribution=None):
    trial = Path(trial)
    calls_by_role = calls_by_role or all_calls(trial)
    events = json.loads((trial / "instrumentation/assignment_events.json").read_text())
    linked = {}
    for row in (attribution or {}).get("calls", []):
        if row.get("native_call_id"):
            linked[row["native_call_id"]] = row
    out = {}
    for role in SPECIALISTS:
        timeline = [
            dict(
                timestamp=e["timestamp"],
                kind="assignment_seeded_before_spawn",
                detail=e["assignment_id"],
            )
            for e in events
            if e.get("acting_agent") == role
        ]
        for c in calls_by_role.get(role, []):
            tool = short(c["tool_name"])
            args = {k: v for k, v in c["arguments"].items() if k != "team_name"}
            if tool == "task_list":
                kind = "assignment_received" if returned_tasks(c) else "task_board_query"
            elif tool == "task_update":
                kind = {
                    "in_progress": "task_claimed",
                    "completed": "task_marked_completed",
                    "blocked": "task_marked_blocked",
                }.get(args.get("status"), "task_update")
            elif tool.startswith("mailbox_"):
                kind = "mailbox_" + tool.split("_", 1)[1]
            elif c["tool_name"].startswith("mcp__multi_agent_tools__"):
                kind = "finance_call"
            else:
                kind = "native_tool:" + tool
            item = dict(
                timestamp=c.get("timestamp"),
                kind=kind,
                tool=tool,
                arguments=args
                if kind != "mailbox_send"
                else dict(args, content=(args.get("content") or "")[:300]),
                tool_error=bool(c.get("is_error")),
                error_class=error_class(c),
                result_excerpt=(c.get("result") or "")[:240],
                source=c["source_trace_reference"].rsplit("/", 2)[-2:],
            )
            link = linked.get(c.get("tool_call_id"))
            if link:
                item.update(
                    rule_ids=link.get("rule_ids"),
                    research_outcome=link.get("research_outcome"),
                    commit_verification_status=link.get("commit_verification_status"),
                )
            timeline.append(item)
        trace = native_dir(trial) / role / "trace.jsonl"
        for _, e in records(trace):
            if e.get("type") == "result":
                timeline.append(
                    dict(
                        timestamp=e.get("_ha_recv_ts"),
                        kind="final_response",
                        detail=str(e.get("result"))[:600],
                        subtype=e.get("subtype"),
                        num_turns=e.get("num_turns"),
                    )
                )
        timeline.sort(key=lambda x: x.get("timestamp") or 0)
        finance = [t for t in timeline if t["kind"] == "finance_call"]
        out[role] = dict(
            first_call_timestamp=next(
                (
                    t["timestamp"]
                    for t in timeline
                    if t["kind"] != "assignment_seeded_before_spawn"
                ),
                None,
            ),
            hub_messages_received=sum(
                t["kind"] == "mailbox_receive" and "relationship_manager" in t["result_excerpt"]
                for t in timeline
            ),
            finance_calls=len(finance),
            finance_calls_by_tool=dict(Counter(t["tool"] for t in finance)),
            rule_linked_state_changing_calls=sum(bool(t.get("rule_ids")) for t in finance),
            timeline=timeline,
        )
    return out


def audit_trial(trial, output):
    """Write all derived coordination evidence for one trial to ``output`` (never ``trial``)."""
    from .effect_attribution import attribute_effects

    trial, output = Path(trial).resolve(), Path(output).resolve()
    if output == trial or trial in output.parents:
        raise ValueError("Derived audit output must be outside the trial evidence folder")
    output.mkdir(parents=True, exist_ok=True)
    before = {str(p): sha256(p) for p in trial.rglob("*") if p.is_file()}
    calls_by_role = all_calls(trial)
    hub = role_ledger(trial, HUB, calls_by_role)
    summary = ledger_summary(trial, HUB, hub, calls_by_role)
    attribution = attribute_effects(trial)
    results = dict(
        coordinator_call_ledger=dict(summary=summary, calls=hub),
        coordination_progress=progress_diagnostic(trial, calls_by_role),
        specialist_timelines=specialist_timelines(trial, calls_by_role, attribution),
        effect_attribution=attribution,
    )
    for name, data in results.items():
        (output / f"{name}.json").write_text(json.dumps(data, indent=2, default=str) + "\n")
    after = {str(p): sha256(p) for p in trial.rglob("*") if p.is_file()}
    receipt = dict(
        trial=str(trial),
        trial_files_hashed=len(before),
        trial_unchanged=before == after,
        outputs=sorted(f"{n}.json" for n in results),
    )
    (output / "audit_receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    if not receipt["trial_unchanged"]:
        raise RuntimeError("Trial evidence changed during a read-only audit")
    return results
