#!/usr/bin/env python3
"""Verify a real qwen_code ClawTeam run against the integration criteria.

Reads only artifacts produced by the run itself:
  * the result JSON (``--results``) and normalized trace JSONL it points to,
  * the exported native Qwen Code artifacts (per-agent stream-json, system prompt,
    MCP config, egress log) referenced by ``inference_provenance.native_artifacts``,
  * the task YAML (for roles and role instructions).

Usage: verify_clawteam_run.py RESULT_JSON [--out verdict.json]
Exit 0 only if every criterion passes.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TASK_ROOTS = (REPO / "multi_agent" / "tasks", REPO / "tests" / "fixtures" / "synthetic")
sys.path.insert(0, str(REPO))


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    if path.exists():
        for line in path.read_text(errors="replace").splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return rows


def tool_events(stream: list[dict]) -> list[tuple[str, dict, dict | None]]:
    """(name, input, result_block) for every tool_use in a Qwen stream."""
    calls, results = [], {}
    for ev in stream:
        for block in ((ev.get("message") or {}).get("content") or []):
            if not isinstance(block, dict):
                continue
            if ev.get("type") == "assistant" and block.get("type") == "tool_use":
                calls.append((block.get("id"), block.get("name", ""), block.get("input") or {}))
            if ev.get("type") == "user" and block.get("type") == "tool_result":
                results[block.get("tool_use_id")] = block
    return [(name, args, results.get(cid)) for cid, name, args in calls]


def ok_result(block: dict | None) -> bool:
    if not block or block.get("is_error"):
        return False
    text = json.dumps(block.get("content"))
    return "not found in registry" not in text and "Error executing tool" not in text


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("result_json")
    ap.add_argument("--out")
    ap.add_argument("--task", help="task YAML (default: found by task_id)")
    args = ap.parse_args()

    from multi_agent.loader import load_task_with_tools

    report = json.loads(Path(args.result_json).read_text())
    prov = report.get("inference_provenance") or {}
    native = Path(prov.get("native_artifacts") or "")
    trace = load_jsonl(Path(report["trace_file"]))
    task_file = next((e for e in trace if e.get("type") == "trace_start"), {})
    task_yaml = Path(args.task) if args.task else next(
        p for root in TASK_ROOTS
        for p in root.rglob(f"{report['task_id']}.yaml"))
    task, _ = load_task_with_tools(task_yaml)
    roles = [a.role for a in task.agents]
    hub, spokes = roles[0], roles[1:]
    streams = {r: load_jsonl(native / r / "trace.jsonl") for r in roles}
    tools = {r: tool_events(s) for r, s in streams.items()}

    def started(role: str) -> bool:
        return any(e.get("type") == "system" and e.get("subtype") == "init"
                   for e in streams[role])

    def calls(role: str, suffix: str, ok: bool = True) -> list:
        return [t for t in tools[role] if t[0].endswith(suffix) and (ok_result(t[2]) or not ok)]

    checks: dict[str, tuple[bool, str]] = {}
    checks["1_coordinator_started"] = (started(hub), f"{hub} init event present")
    checks["2_specialists_started"] = (
        all(started(r) for r in spokes),
        ", ".join(f"{r}={'yes' if started(r) else 'NO'}" for r in spokes))
    instr = {}
    for agent in task.agents:
        text = (native / agent.role / "system_prompt.md")
        content = text.read_text() if text.exists() else ""
        expected = (agent.system_prompt or agent.description or "").strip()[:60]
        instr[agent.role] = bool(expected) and expected in content
    checks["3_role_instructions_delivered"] = (all(instr.values()), json.dumps(instr))
    listed = {r: len(calls(r, "task_list")) for r in spokes}
    checks["4_agents_list_assignments"] = (
        all(v > 0 for v in listed.values()), f"successful task_list per specialist {listed}")
    sent = {r: len(calls(r, "mailbox_send")) for r in roles}
    received = len([t for t in calls(hub, "mailbox_receive")
                    if t[2] and "[]" not in json.dumps(t[2].get("content"))])
    checks["5_mailbox_send_and_receive"] = (
        sum(sent.values()) > 0 and received > 0,
        f"successful mailbox_send per role {sent}; non-empty hub mailbox_receive={received}")
    domain = [e for e in trace if e.get("type") == "tool_call"]
    checks["6_agents_call_mcp_domain_tools"] = (
        len(domain) > 0, f"{len(domain)} normalized domain tool calls: "
        + ", ".join(sorted({e['tool_name'] for e in domain})))
    good = [e for e in domain if e.get("tool_result") and "(no result recorded)"
            not in e["tool_result"] and not (e.get("raw_event") or {}).get("is_error")]
    checks["7_tool_results_returned"] = (
        len(good) == len(domain) and bool(domain),
        f"{len(good)}/{len(domain)} domain calls paired with a non-error result")
    checks["8_native_traces_collected"] = (
        all(streams[r] for r in roles),
        ", ".join(f"{r}:{len(streams[r])} events" for r in roles))
    comms = [e for e in trace if e.get("type") == "communication"]
    checks["9_normalized_actions_generated"] = (
        bool(domain) and bool(comms),
        f"{len(domain)} tool_call + {len(comms)} communication events in {report['trace_file']}")
    decisions = [e for e in trace if e.get("type") == "access_decision"]
    checks["10_checker_evaluated_real_events"] = (
        len(decisions) >= len(domain) + len([c for c in comms if c.get("target_role") != "user"])
        and decisions != [],
        f"{len(decisions)} access decisions; n_violations={report.get('n_violations')}")
    egress: dict[str, int] = {}
    non_loopback: list[str] = []
    for role in roles:
        for rec in load_jsonl(native / role / "netlog.jsonl"):
            if rec.get("kind") == "tcp":
                key = f"{rec.get('remote')}:{rec.get('port')}"
                egress[key] = egress.get(key, 0) + 1
                if not str(rec.get("remote", "")).replace("::ffff:", "").startswith("127."):
                    non_loopback.append(key)
            if rec.get("kind") == "dns":
                non_loopback.append(f"dns:{rec.get('host')}")
    checks["11_network_egress_loopback_only"] = (
        bool(egress) and not non_loopback,
        f"connections {egress}; non-loopback {non_loopback or 'none'}")

    verdict = {
        "result_json": args.result_json,
        "run_id": report.get("run_id"),
        "native_artifacts": str(native),
        "checks": {k: {"pass": v[0], "detail": v[1]} for k, v in checks.items()},
        "all_pass": all(v[0] for v in checks.values()),
        "trace_start_model": task_file.get("model"),
    }
    for name, (passed, detail) in checks.items():
        print(f"{'PASS' if passed else 'FAIL'}  {name:<36} {detail}")
    print("ALL CRITERIA PASS" if verdict["all_pass"] else "SOME CRITERIA FAILED")
    if args.out:
        Path(args.out).write_text(json.dumps(verdict, indent=2))
    return 0 if verdict["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
