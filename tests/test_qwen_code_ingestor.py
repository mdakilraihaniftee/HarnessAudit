"""Qwen Code stream-json ingestion, on real traces captured from a local fin-t1 run.

Fixtures: tests/fixtures/qwen_code/*.jsonl are unmodified stream-json files written
by the qwen_code harness wrapper (Qwen Code 0.27.4, Qwen/Qwen3-8B via vLLM 0.33.0).
"""

from __future__ import annotations

import json
from pathlib import Path

from multi_agent.frameworks.core.action_sink import ActionSink
from multi_agent.frameworks.native.qwen_code.ingestor import (
    collect_qwen_traces,
    parse_qwen_stream,
    read_final_text,
)

FIX = Path(__file__).parent / "fixtures" / "qwen_code"
CATALOG = {
    "get_client_profile", "get_portfolio_summary", "get_market_data", "get_trade_history",
    "check_suitability", "set_risk_limits", "execute_trade", "submit_compliance_report",
}
ROLES = ["relationship_manager", "risk_officer", "trader", "compliance_analyst"]


def _raw_pairs(path: Path) -> list[tuple[str, dict, dict]]:
    pending, pairs = {}, []
    for line in path.read_text().splitlines():
        ev = json.loads(line)
        for block in (ev.get("message") or {}).get("content") or []:
            if ev["type"] == "assistant" and block.get("type") == "tool_use":
                pending[block["id"]] = (block["name"], block["input"])
            if ev["type"] == "user" and block.get("type") == "tool_result":
                name, args = pending.pop(block["tool_use_id"])
                pairs.append((name, args, block))
    return pairs


def _team(path: Path) -> str:
    for name, args, _ in _raw_pairs(path):
        if "team_name" in args:
            return args["team_name"]
    raise AssertionError("no team name in fixture")


def test_domain_tool_calls_are_paired_and_normalized():
    path = FIX / "compliance_analyst.jsonl"
    sink = ActionSink(run_id="r")
    summary = parse_qwen_stream(path, "compliance_analyst", CATALOG, ROLES, sink,
                                expected_team_name=_team(path))
    actions = sink.actions
    expected = [(n.split("__")[-1], a, b) for n, a, b in _raw_pairs(path)
                if n.startswith("mcp__multi_agent_tools__")]
    tool_actions = [a for a in actions if a.surface == "tool_call"]
    assert len(tool_actions) == len(expected) == summary["tool_calls"] > 0
    for action, (name, args, block) in zip(
        sorted(tool_actions, key=lambda a: a.raw_event["call_line"]),
        expected,
    ):
        assert action.tool_name == name and action.tool_args == args
        assert action.tool_result.startswith(str(block["content"])[:20])
        assert action.raw_event["harness"] == "qwen_code"
        assert action.raw_event["timestamp_source"] == "wrapper_receive_time"
    assert all(a.agent_role == "compliance_analyst" for a in actions)


def test_successful_mailbox_send_becomes_communication():
    path = FIX / "trader.jsonl"
    sink = ActionSink(run_id="r")
    parse_qwen_stream(path, "trader", CATALOG, ROLES, sink, expected_team_name=_team(path))
    sends = [(a, b) for n, a, b in _raw_pairs(path) if n.endswith("__mailbox_send")
             and not b.get("is_error")]
    comms = [a for a in sink.actions if a.surface == "communication"]
    assert sends and len(comms) == len(sends)
    assert comms[0].target_role == sends[0][0]["to"]
    assert comms[0].content == sends[0][0].get("content", "")


def test_wrong_team_mailbox_is_ignored():
    sink = ActionSink(run_id="r")
    parse_qwen_stream(FIX / "trader.jsonl", "trader", CATALOG, ROLES, sink,
                      expected_team_name="multi-agent-some-other-run")
    assert not [a for a in sink.actions if a.surface == "communication"]


def test_timestamps_come_from_receive_time_and_are_ordered():
    sink = ActionSink(run_id="r")
    collect_qwen_traces(
        traces_by_role={"trader": FIX / "trader.jsonl",
                        "compliance_analyst": FIX / "compliance_analyst.jsonl"},
        catalog_tool_names=CATALOG, all_agent_roles=ROLES, action_sink=sink,
        hub_role="relationship_manager")
    stamps = [a.timestamp for a in sink.actions]
    assert stamps == sorted(stamps) and stamps[0].year >= 2026
    assert [a.sequence_num for a in sink.actions] == list(range(1, len(stamps) + 1))


def test_orphan_tool_use_is_recorded_not_dropped(tmp_path):
    lines = (FIX / "compliance_analyst.jsonl").read_text().splitlines()
    first_use = next(i for i, ln in enumerate(lines)
                     if '"tool_use"' in ln and "multi_agent_tools" in ln)
    truncated = tmp_path / "t.jsonl"
    truncated.write_text("\n".join(lines[: first_use + 1]) + "\n")
    sink = ActionSink(run_id="r")
    summary = parse_qwen_stream(truncated, "compliance_analyst", CATALOG, ROLES, sink)
    assert summary["orphan_tool_calls"] == 1
    assert sink.actions[-1].tool_result == "(no result recorded)"


def test_final_text_from_result_event():
    text = read_final_text(FIX / "compliance_analyst.jsonl")
    result = [json.loads(ln) for ln in (FIX / "compliance_analyst.jsonl").read_text()
              .splitlines() if '"type":"result"' in ln][-1]
    assert text == result["result"]


def test_checker_runs_unchanged_on_ingested_actions():
    from multi_agent.checker import check_trace
    from multi_agent.loader import load_task_with_tools

    task, _ = load_task_with_tools("multi_agent/tasks/finance/financial_planning/fin-t1.yaml")
    sink = ActionSink(run_id="r")
    collect_qwen_traces(
        traces_by_role={"trader": FIX / "trader.jsonl"}, catalog_tool_names=CATALOG,
        all_agent_roles=ROLES, action_sink=sink, hub_role="relationship_manager")
    decisions = check_trace(sink.actions, task.access_rules, domain="finance")
    assert len(decisions) >= len(sink.actions)
    # trader is not authorised for set_risk_limits in fin-t1: the checker must flag it
    flagged = {d.action_sequence_num for d in decisions if d.violated}
    risk_calls = {a.sequence_num for a in sink.actions if a.tool_name == "set_risk_limits"}
    assert risk_calls and risk_calls <= flagged
