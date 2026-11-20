"""Qwen Code stream-json ingestion for HarnessAudit.

Each agent's stdout (``--output-format stream-json``) is captured by the wrapper
with a receive timestamp per line (``_ha_recv_ts``). Verified event shapes
(Qwen Code 0.27.4):

* ``{"type":"system","subtype":"init","tools":[...],"mcp_servers":[{"name","status"}]}``
* ``{"type":"assistant","message":{"model":..,"content":[{"type":"text","text":..},
  {"type":"tool_use","id":..,"name":"mcp__<server>__<tool>","input":{..}}],"usage":{..}}}``
* ``{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":..,
  "is_error":bool,"content":str|[blocks]}]}}``
* ``{"type":"result","subtype":"success"|...,"is_error":bool,"result":str,"usage":{..}}``

Tool calls are paired with results by ``tool_use_id``; domain MCP calls become
``tool_call`` actions, successful ClawTeam mailbox sends/broadcasts become
``communication`` actions — the same normalization rules as the Codex and
Claude ingestors, so checker semantics are unchanged.

Missing observability (recorded, never invented): stream-json has no native
per-event timestamp, so ``timestamp_source`` is ``wrapper_receive_time``.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from ...core.sde_policy import native_bypass_tool_name
from ...core.trace_semantics import (
    is_mailbox_broadcast,
    is_mailbox_send,
    mailbox_call_succeeded,
    mailbox_recipients,
    mailbox_team_name,
    normalize_mcp_tool_name,
)

log = logging.getLogger(__name__)

MCP_SERVER_NAME = "multi_agent_tools"
SDE_QWEN_NATIVE_TOOLS = frozenset({
    "run_shell_command", "write_file", "edit", "replace", "read_file", "read_many_files",
    "list_directory", "glob", "grep_search", "web_fetch", "web_search",
})


def _iter_events(path: Path) -> Iterator[tuple[int, dict[str, Any]]]:
    if not path.exists():
        return
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line_no, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                yield line_no, record


def _ts(record: dict[str, Any]) -> str | None:
    value = record.get("_ha_recv_ts")
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def _content_blocks(record: dict[str, Any]) -> list[dict[str, Any]]:
    message = record.get("message") or {}
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return [block for block in content if isinstance(block, dict)]
    return []


def _result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text" or "text" in block:
                    parts.append(str(block.get("text", "")))
                else:
                    parts.append(json.dumps(block, ensure_ascii=False))
            else:
                parts.append(str(block))
        return "\n".join(parts)
    if content is None:
        return ""
    return json.dumps(content, ensure_ascii=False, default=str)


def read_final_text(path: Path) -> str:
    """Final assistant text: the ``result`` event, else the last assistant text."""
    final = ""
    last_text = ""
    for _, record in _iter_events(path):
        kind = record.get("type")
        if kind == "assistant":
            for block in _content_blocks(record):
                if block.get("type") == "text" and str(block.get("text", "")).strip():
                    last_text = str(block["text"])
        elif kind == "result" and not record.get("is_error"):
            text = record.get("result")
            if isinstance(text, str) and text.strip():
                final = text
    return final or last_text


def _raw_event(rollout: Path, line_no: int, mode: str, **extra: Any) -> dict[str, Any]:
    return {
        "framework": "clawteam",
        "harness": "qwen_code",
        "capture_mode": mode,
        "source_file": str(rollout),
        "source_line": line_no,
        "timestamp_source": "wrapper_receive_time",
        **extra,
    }


def parse_qwen_stream(
    trace_path: Path,
    agent_role: str,
    catalog_tool_names: set[str],
    all_agent_roles: list[str],
    action_sink: Any,
    *,
    expected_team_name: str | None = None,
    sde_mcp_only: bool = False,
    mcp_server_name: str = MCP_SERVER_NAME,
) -> dict[str, Any]:
    """Emit ObservableActions for one agent; return a per-agent summary."""
    pending: dict[str, dict[str, Any]] = {}
    summary: dict[str, Any] = {
        "role": agent_role, "trace": str(trace_path), "tool_calls": 0, "communications": 0,
        "native_tool_calls": 0, "models": [], "mcp_servers": [], "result": None,
        "final_text": "", "is_error": False, "error": None, "orphan_tool_calls": 0,
    }
    last_text = ""

    for line_no, record in _iter_events(trace_path):
        kind = record.get("type")
        if kind == "system" and record.get("subtype") == "init":
            summary["mcp_servers"] = record.get("mcp_servers") or []
            failed = [s for s in summary["mcp_servers"]
                      if isinstance(s, dict) and s.get("status") != "connected"]
            if failed:
                log.warning("qwen_code agent %s: MCP server(s) not connected: %s",
                            agent_role, failed)
        elif kind == "assistant":
            message = record.get("message") or {}
            model = message.get("model") if isinstance(message, dict) else None
            if model and model not in summary["models"]:
                summary["models"].append(model)
            for block in _content_blocks(record):
                btype = block.get("type")
                if btype == "text" and str(block.get("text", "")).strip():
                    last_text = str(block["text"])
                elif btype == "tool_use":
                    pending[str(block.get("id", ""))] = {
                        "name": str(block.get("name", "")),
                        "args": block.get("input") if isinstance(block.get("input"), dict)
                        else {"_raw": block.get("input")},
                        "timestamp": _ts(record),
                        "line_no": line_no,
                    }
        elif kind == "user":
            for block in _content_blocks(record):
                if block.get("type") != "tool_result":
                    continue
                call = pending.pop(str(block.get("tool_use_id", "")), None)
                if call is None:
                    continue
                _emit(
                    call, block, record, line_no, trace_path, agent_role, catalog_tool_names,
                    all_agent_roles, action_sink, summary, expected_team_name,
                    sde_mcp_only, mcp_server_name,
                )
        elif kind == "result":
            summary["result"] = record.get("subtype")
            summary["is_error"] = bool(record.get("is_error"))
            summary["usage"] = record.get("usage")
            summary["num_turns"] = record.get("num_turns")
            summary["duration_ms"] = record.get("duration_ms")
            if record.get("is_error"):
                summary["error"] = str(record.get("error") or record.get("result") or
                                       record.get("subtype"))
            elif isinstance(record.get("result"), str) and record["result"].strip():
                last_text = record["result"]

    for call in pending.values():   # tool_use without a result (truncated session)
        name = normalize_mcp_tool_name(call["name"], mcp_server_name)
        if name in catalog_tool_names:
            summary["orphan_tool_calls"] += 1
            action_sink.emit_tool_call(
                agent_id=agent_role, agent_role=agent_role, tool_name=name,
                tool_args=call["args"], tool_result="(no result recorded)",
                raw_event=_raw_event(trace_path, call["line_no"], "qwen_stream_orphan_tool_use",
                                     raw_tool_name=call["name"]),
                timestamp=call["timestamp"],
            )
    summary["final_text"] = last_text
    return summary


def _emit(
    call: dict[str, Any], block: dict[str, Any], record: dict[str, Any], line_no: int,
    trace_path: Path, agent_role: str, catalog: set[str], all_roles: list[str],
    action_sink: Any, summary: dict[str, Any], expected_team: str | None,
    sde_mcp_only: bool, server_name: str,
) -> None:
    raw_name = call["name"]
    args = call["args"] if isinstance(call["args"], dict) else {}
    result_text = _result_text(block.get("content"))
    is_error = bool(block.get("is_error"))
    timestamp = call["timestamp"] or _ts(record)
    name = normalize_mcp_tool_name(raw_name, server_name)
    if name in catalog:
        summary["tool_calls"] += 1
        action_sink.emit_tool_call(
            agent_id=agent_role, agent_role=agent_role, tool_name=name, tool_args=args,
            tool_result=result_text,
            raw_event=_raw_event(trace_path, line_no, "qwen_stream_tool_result",
                                 raw_tool_name=raw_name, is_error=is_error,
                                 call_line=call["line_no"]),
            timestamp=timestamp,
        )
        return
    if is_mailbox_send(raw_name) or is_mailbox_broadcast(raw_name):
        if not mailbox_call_succeeded(result_text, is_error=is_error):
            return
        team = mailbox_team_name(args)
        if expected_team and team != expected_team:
            return
        kind = "broadcast" if is_mailbox_broadcast(raw_name) else "send"
        for recipient in mailbox_recipients(
            kind=kind, sender_role=agent_role, all_agent_roles=all_roles, tool_args=args,
        ):
            extra: dict[str, Any] = {"tool": raw_name, "team_name": team}
            if kind == "broadcast":
                extra["expanded_from"] = "broadcast"
            summary["communications"] += 1
            action_sink.emit_communication(
                agent_id=agent_role, agent_role=agent_role, target_agent=recipient,
                target_role=recipient, content=str(args.get("content", "")),
                raw_event=_raw_event(trace_path, line_no, f"qwen_stream_mailbox_{kind}", **extra),
                timestamp=timestamp,
            )
        return
    summary["native_tool_calls"] += 1
    short = raw_name.rsplit("__", 1)[-1]
    if sde_mcp_only and short in SDE_QWEN_NATIVE_TOOLS:
        action_sink.emit_tool_call(
            agent_id=agent_role, agent_role=agent_role,
            tool_name=native_bypass_tool_name(raw_name), tool_args=args,
            tool_result="(SDE native harness tool bypass attempt)",
            raw_event=_raw_event(trace_path, line_no, "qwen_stream_sde_native_bypass",
                                 raw_tool_name=raw_name, sde_native_bypass_detected=True),
            timestamp=timestamp,
        )


def collect_qwen_traces(
    *,
    traces_by_role: dict[str, Path],
    catalog_tool_names: set[str],
    all_agent_roles: list[str],
    action_sink: Any,
    hub_role: str,
    run_id: str = "",
    sde_mcp_only: bool = False,
) -> str:
    """Parse every agent's stream; return the hub's final text."""
    final_text = ""
    expected_team = f"multi-agent-{run_id}" if run_id else None
    hub_error = None
    for role, path in traces_by_role.items():
        if not path.exists() or path.stat().st_size == 0:
            log.warning("No Qwen Code trace for agent %s at %s", role, path)
            continue
        summary = parse_qwen_stream(
            path, role, catalog_tool_names, all_agent_roles, action_sink,
            expected_team_name=expected_team, sde_mcp_only=sde_mcp_only,
        )
        log.info(
            "qwen_code %s: %d tool call(s), %d communication(s), result=%s models=%s",
            role, summary["tool_calls"], summary["communications"], summary["result"],
            summary["models"],
        )
        if role == hub_role:
            final_text = summary["final_text"]
            if summary["is_error"] and not final_text:
                hub_error = summary["error"]
        elif summary["is_error"]:
            log.warning("qwen_code agent %s ended with an error: %s", role, summary["error"])
    if hub_error:
        raise RuntimeError(f"Qwen Code hub agent {hub_role} failed: {hub_error}")
    return final_text


def summarize_agent(trace: Path, netlog: Path, home: Path) -> dict[str, Any]:
    """Usage + network egress summary for one agent (for cost evidence and perf)."""
    from multi_agent.local_inference.endpoints import is_loopback_host

    out: dict[str, Any] = {"trace_events": 0, "models": [], "usage": None,
                           "egress": {"tcp": {}, "dns": [], "non_loopback": []}}
    for _, record in _iter_events(trace):
        out["trace_events"] += 1
        if record.get("type") == "assistant":
            model = (record.get("message") or {}).get("model")
            if model and model not in out["models"]:
                out["models"].append(model)
        if record.get("type") == "result":
            out["usage"] = record.get("usage")
            out["num_turns"] = record.get("num_turns")
            out["duration_ms"] = record.get("duration_ms")
            out["duration_api_ms"] = record.get("duration_api_ms")
            out["result_subtype"] = record.get("subtype")
    usage_file = home / "usage_record.jsonl"
    if usage_file.exists():
        records = [r for _, r in _iter_events(usage_file)]
        if records:
            out["model_requests"] = {
                model: stats.get("requests")
                for model, stats in (records[-1].get("models") or {}).items()
            }
            out["usage_record"] = records[-1].get("models")
    if netlog.exists():
        for _, rec in _iter_events(netlog):
            if rec.get("kind") == "tcp":
                key = f"{rec.get('remote')}:{rec.get('port')}"
                out["egress"]["tcp"][key] = out["egress"]["tcp"].get(key, 0) + 1
                if not is_loopback_host(str(rec.get("remote", "")).replace("::ffff:", "")):
                    out["egress"]["non_loopback"].append(key)
            elif rec.get("kind") == "dns":
                out["egress"]["dns"].append(rec.get("host"))
        out["egress"]["netlog_present"] = True
    else:
        out["egress"]["netlog_present"] = False
    return out
