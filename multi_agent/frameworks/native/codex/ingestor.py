"""Codex trace discovery and parsing for HarnessAudit.

Codex stores session rollouts as JSONL files at::

    $CODEX_HOME/sessions/{YYYY}/{MM}/{DD}/rollout-{datetime}-{thread_id}.jsonl

Each line is a ``RolloutItem`` variant (verified via Phase 0 probe):

- ``session_meta``   — session metadata (cwd, model, instructions)
- ``response_item``  — model messages (assistant, user, developer) and tool calls
- ``event_msg``      — lifecycle events (task_started, task_complete, user_message)
- ``turn_context``   — turn metadata (model, policy, timestamps)

Tool calls appear as ``ResponseItem::FunctionCall`` with:

- ``name``       — qualified tool name (e.g. ``mcp__multi_agent_tools__execute_trade``)
- ``arguments``  — JSON string of tool arguments
- ``call_id``    — ID for pairing with ``FunctionCallOutput``

Tool results appear as ``ResponseItem::FunctionCallOutput`` with:

- ``call_id``    — matches the ``FunctionCall``
- ``output``     — result text or structured content

This is structurally similar to Claude Code's tool_use/tool_result pairing,
so the same ActionSink emit pattern applies.

.. note::

    Tool call format inferred from ``codex-rs/protocol/src/models.rs``
    (``ResponseItem`` enum).  Phase 0 probe captured session JSONL path
    and structure, but API key was expired so no real tool call events
    were captured.  Parser needs validation with a real Codex run.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ...core.sde_policy import is_sde_native_tool, native_bypass_tool_name
from ...core.trace_semantics import (
    is_mailbox_broadcast as _is_mailbox_broadcast,
)
from ...core.trace_semantics import (
    is_mailbox_send as _is_mailbox_send,
)
from ...core.trace_semantics import (
    mailbox_call_succeeded,
    mailbox_recipients,
    mailbox_team_name,
)
from ...core.trace_semantics import (
    normalize_mcp_tool_name as _strip_mcp_prefix,
)

log = logging.getLogger(__name__)

MCP_SERVER_NAME = "multi_agent_tools"


# ---- Artifact discovery ----


def _find_codex_sessions(
    agent_dir: Path,
    agent_role: str,
    *,
    created_after: float = 0.0,
    sessions_dir: Path | None = None,
    match_cwd: bool = True,
    warn_on_miss: bool = True,
) -> list[Path]:
    """Find Codex rollout JSONL files for an agent.

    Codex agents in isolated multi_agent runs share the runtime's
    ``$CODEX_HOME/sessions`` directory.  Legacy runs may still use
    ``~/.codex/sessions``.  We find sessions by scanning rollout files
    created after spawn_timestamp.

    Legacy runs can place multiple agents in one sessions directory, so those
    rollouts are matched by the cwd field in ``session_meta``.  Current runs
    give every role a fresh, isolated ``CODEX_HOME``; callers can disable cwd
    matching for those directories because the directory itself identifies
    the agent.  This also accommodates Codex versions that record a resolved
    or host workspace cwd instead of the subprocess cwd.
    """
    sessions_dir = sessions_dir or Path.home() / ".codex" / "sessions"

    if not sessions_dir.exists():
        if warn_on_miss:
            log.warning("No Codex sessions dir at %s", sessions_dir)
        return []

    candidates = sorted(
        (
            f
            for f in sessions_dir.rglob("rollout-*.jsonl")
            if created_after == 0.0 or f.stat().st_mtime > created_after
        ),
        key=lambda path: (path.stat().st_mtime_ns, str(path)),
        reverse=True,
    )
    if not candidates:
        if warn_on_miss:
            log.warning(
                "No Codex rollout files (after ts=%.0f) in %s", created_after, sessions_dir
            )
        return []

    if not match_cwd:
        # The per-role CODEX_HOME is created empty for this run.  Returning
        # only the newest rollout preserves the one-session-per-spawn contract
        # if a Codex version happens to leave an auxiliary rollout behind.
        return candidates[:1]

    # Shared/legacy sessions need cwd matching to avoid attributing another
    # agent's rollout to this role.
    agent_cwd = str(agent_dir)
    for f in candidates:
        try:
            with f.open() as handle:
                first_line = handle.readline()
            meta = json.loads(first_line)
            if meta.get("type") == "session_meta":
                rollout_cwd = meta.get("payload", {}).get("cwd", "")
                if rollout_cwd == agent_cwd:
                    return [f]
        except (json.JSONDecodeError, OSError):
            continue

    if warn_on_miss:
        log.warning("No Codex rollout matched cwd=%s for agent %s", agent_cwd, agent_role)
    return []


# ---- Rollout JSONL parsing ----


def _parse_codex_rollout(
    rollout_path: Path,
    agent_role: str,
    catalog_tool_names: set[str],
    all_agent_roles: list[str],
    action_sink: Any,
    *,
    mcp_server_name: str = MCP_SERVER_NAME,
    expected_team_name: str | None = None,
    sde_mcp_only: bool = False,
) -> str:
    """Parse one Codex rollout JSONL and emit ObservableActions.

    Returns the last assistant text (for hub final output extraction).
    """
    last_assistant_text = ""
    terminal_error = ""
    # call_id → {name, args, raw_name}
    pending_tool_calls: dict[str, dict[str, Any]] = {}
    pending_mailbox_calls: dict[str, dict[str, Any]] = {}

    def _record_timestamp(record: dict[str, Any]) -> str | None:
        ts = record.get("timestamp")
        return str(ts) if ts else None

    with open(rollout_path) as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue

            record_type = record.get("type", "")

            if record_type == "event_msg":
                payload = record.get("payload", {})
                if payload.get("type") == "task_complete" and payload.get("error"):
                    raw_error = payload["error"]
                    if isinstance(raw_error, dict):
                        raw_error = raw_error.get("message") or json.dumps(raw_error)
                    terminal_error = str(raw_error)
                    try:
                        decoded = json.loads(terminal_error)
                        nested = decoded.get("error", {})
                        terminal_error = str(nested.get("message") or terminal_error)
                    except (json.JSONDecodeError, AttributeError):
                        pass

                # Codex 0.156+ records MCP calls executed through its unified
                # exec tool as structured completed items.  These replace the
                # older response_item function_call/function_call_output pair.
                item = payload.get("item", {})
                if payload.get("type") == "item_completed" and item.get("type") == "McpToolCall":
                    server = str(item.get("server", ""))
                    tool_name = str(item.get("tool", ""))
                    raw_name = f"mcp__{server}__{tool_name}"
                    tool_args = item.get("arguments", {})
                    if not isinstance(tool_args, dict):
                        tool_args = {"_raw": tool_args}
                    result = item.get("result", {})
                    if isinstance(result, dict):
                        content = result.get("content", [])
                        parts = [
                            str(part.get("text", ""))
                            for part in content
                            if isinstance(part, dict) and part.get("text")
                        ]
                        result_text = "\n".join(parts) or json.dumps(result, default=str)
                    else:
                        result_text = str(result)

                    normalized_name = _strip_mcp_prefix(raw_name, mcp_server_name)
                    if normalized_name in catalog_tool_names:
                        action_sink.emit_tool_call(
                            agent_id=agent_role,
                            agent_role=agent_role,
                            tool_name=normalized_name,
                            tool_args=tool_args,
                            tool_result=result_text,
                            raw_event={
                                "framework": "clawteam",
                                "harness": "codex",
                                "raw_tool_name": raw_name,
                                "capture_mode": "codex_rollout_mcp_item_completed",
                                "source_file": str(rollout_path),
                                "source_line": line_no,
                            },
                            timestamp=_record_timestamp(record),
                        )
                    elif _is_mailbox_send(raw_name) or _is_mailbox_broadcast(raw_name):
                        is_broadcast = _is_mailbox_broadcast(raw_name)
                        is_error = (
                            bool(result.get("isError")) if isinstance(result, dict) else False
                        )
                        if not mailbox_call_succeeded(
                            result_text,
                            is_error=is_error,
                            status=str(item.get("status", "")),
                        ):
                            continue
                        team_name = mailbox_team_name(tool_args)
                        if expected_team_name and team_name != expected_team_name:
                            continue
                        kind = "broadcast" if is_broadcast else "send"
                        for recipient in mailbox_recipients(
                            kind=kind,
                            sender_role=agent_role,
                            all_agent_roles=all_agent_roles,
                            tool_args=tool_args,
                        ):
                            raw_event = {
                                "framework": "clawteam",
                                "harness": "codex",
                                "tool": raw_name,
                                "capture_mode": f"codex_rollout_mcp_{kind}",
                                "team_name": team_name,
                                "source_file": str(rollout_path),
                                "source_line": line_no,
                            }
                            if is_broadcast:
                                raw_event["expanded_from"] = "broadcast"
                            action_sink.emit_communication(
                                agent_id=agent_role,
                                agent_role=agent_role,
                                target_agent=recipient,
                                target_role=recipient,
                                content=str(tool_args.get("content", "")),
                                raw_event=raw_event,
                                timestamp=_record_timestamp(record),
                            )

            # ---- response_item: contains model messages and tool calls ----
            if record_type == "response_item":
                payload = record.get("payload", {})
                item_type = payload.get("type", "")

                if item_type == "message":
                    role = payload.get("role", "")
                    content = payload.get("content", [])
                    if not isinstance(content, list):
                        continue

                    if role == "assistant":
                        # Extract text and (potentially) tool calls from assistant messages
                        for item in content:
                            if not isinstance(item, dict):
                                continue
                            ctype = item.get("type", "")
                            if ctype == "output_text":
                                text = item.get("text", "")
                                if text:
                                    last_assistant_text = text

                # ---- FunctionCall: tool invocation ----
                elif item_type == "function_call":
                    raw_name = payload.get("name", "")
                    arguments_str = payload.get("arguments", "{}")
                    call_id = payload.get("call_id", "")

                    try:
                        tool_args = (
                            json.loads(arguments_str)
                            if isinstance(arguments_str, str)
                            else arguments_str
                        )
                    except json.JSONDecodeError:
                        tool_args = {"_raw": arguments_str}

                    tool_name = _strip_mcp_prefix(raw_name, mcp_server_name)

                    if tool_name in catalog_tool_names:
                        pending_tool_calls[call_id] = {
                            "name": tool_name,
                            "args": tool_args,
                            "raw_name": raw_name,
                            "timestamp": _record_timestamp(record),
                            "line_no": line_no,
                        }
                    elif _is_mailbox_send(raw_name):
                        pending_mailbox_calls[call_id] = {
                            "kind": "send",
                            "args": tool_args,
                            "raw_name": raw_name,
                            "timestamp": _record_timestamp(record),
                            "line_no": line_no,
                        }
                    elif _is_mailbox_broadcast(raw_name):
                        pending_mailbox_calls[call_id] = {
                            "kind": "broadcast",
                            "args": tool_args,
                            "raw_name": raw_name,
                            "timestamp": _record_timestamp(record),
                            "line_no": line_no,
                        }
                    elif sde_mcp_only and is_sde_native_tool("codex", raw_name):
                        action_sink.emit_tool_call(
                            agent_id=agent_role,
                            agent_role=agent_role,
                            tool_name=native_bypass_tool_name(raw_name),
                            tool_args=tool_args if isinstance(tool_args, dict) else {},
                            tool_result="(SDE native harness tool bypass attempt)",
                            raw_event={
                                "framework": "clawteam",
                                "harness": "codex",
                                "raw_tool_name": raw_name,
                                "capture_mode": "codex_rollout_sde_native_bypass",
                                "sde_native_bypass_detected": True,
                                "source_file": str(rollout_path),
                                "source_line": line_no,
                            },
                            timestamp=_record_timestamp(record),
                        )

                # ---- FunctionCallOutput: tool result ----
                elif item_type == "function_call_output":
                    call_id = payload.get("call_id", "")
                    output = payload.get("output", "")
                    if isinstance(output, dict):
                        # Structured output — extract text content
                        content_items = output.get("content_items", [])
                        if content_items:
                            parts = [
                                ci.get("text", "")
                                for ci in content_items
                                if isinstance(ci, dict) and "text" in ci
                            ]
                            result_text = "\n".join(parts)
                        else:
                            result_text = output.get("content", str(output))
                    else:
                        result_text = str(output)

                    if call_id in pending_tool_calls:
                        tc = pending_tool_calls.pop(call_id)
                        action_sink.emit_tool_call(
                            agent_id=agent_role,
                            agent_role=agent_role,
                            tool_name=tc["name"],
                            tool_args=tc["args"],
                            tool_result=result_text,
                            raw_event={
                                "framework": "clawteam",
                                "harness": "codex",
                                "raw_tool_name": tc["raw_name"],
                                "capture_mode": "codex_rollout_tool_result",
                                "source_file": str(rollout_path),
                                "source_line": line_no,
                            },
                            timestamp=_record_timestamp(record) or tc.get("timestamp"),
                        )
                    elif call_id in pending_mailbox_calls:
                        mc = pending_mailbox_calls.pop(call_id)
                        if not mailbox_call_succeeded(result_text):
                            continue
                        team_name = mailbox_team_name(mc["args"])
                        if expected_team_name and team_name != expected_team_name:
                            continue

                        msg_content = str(mc["args"].get("content", ""))
                        for recipient in mailbox_recipients(
                            kind=mc["kind"],
                            sender_role=agent_role,
                            all_agent_roles=all_agent_roles,
                            tool_args=mc["args"],
                        ):
                            raw_event = {
                                "framework": "clawteam",
                                "harness": "codex",
                                "tool": mc["raw_name"],
                                "capture_mode": (
                                    "codex_rollout_mailbox_send"
                                    if mc["kind"] == "send"
                                    else "codex_rollout_mailbox_broadcast"
                                ),
                                "team_name": team_name,
                                "source_file": str(rollout_path),
                                "source_line": line_no,
                            }
                            if mc["kind"] == "broadcast":
                                raw_event["expanded_from"] = "broadcast"
                            action_sink.emit_communication(
                                agent_id=agent_role,
                                agent_role=agent_role,
                                target_agent=recipient,
                                target_role=recipient,
                                content=msg_content,
                                raw_event=raw_event,
                                timestamp=_record_timestamp(record) or mc.get("timestamp"),
                            )

    # Emit orphan tool calls (shouldn't happen, but guards against truncated sessions)
    for call_id, tc in pending_tool_calls.items():
        action_sink.emit_tool_call(
            agent_id=agent_role,
            agent_role=agent_role,
            tool_name=tc["name"],
            tool_args=tc["args"],
            tool_result="(no result recorded)",
            raw_event={
                "framework": "clawteam",
                "harness": "codex",
                "raw_tool_name": tc["raw_name"],
                "capture_mode": "codex_rollout_orphan_tool_use",
                "source_file": str(rollout_path),
                "source_line": tc.get("line_no", 0),
            },
            timestamp=tc.get("timestamp"),
        )

    if terminal_error and not last_assistant_text:
        raise RuntimeError(f"Codex agent {agent_role} failed: {terminal_error}")

    return last_assistant_text


# ---- Top-level collection entry point ----


def collect_codex_traces(
    worktree_map: dict[str, Path],
    catalog_tool_names: set[str],
    all_agent_roles: list[str],
    action_sink: Any,
    hub_role: str,
    *,
    run_id: str = "",
    spawn_timestamp: float = 0.0,
    sessions_dir: Path | None = None,
    sessions_dirs_by_role: dict[str, Path] | None = None,
    sde_mcp_only: bool = False,
) -> str:
    """Collect and parse Codex rollout traces for all agents in a run.

    Returns the hub agent's final output text.
    """
    final_text = ""

    for agent_role, wt_path in worktree_map.items():
        if sessions_dirs_by_role is not None:
            agent_sessions_dir = sessions_dirs_by_role.get(agent_role)
            if agent_sessions_dir is None:
                log.warning("No isolated Codex sessions dir registered for agent %s", agent_role)
                continue
            created_after_for_role = 0.0
        else:
            agent_sessions_dir = sessions_dir
            created_after_for_role = spawn_timestamp
        rollout_files = _find_codex_sessions(
            wt_path,
            agent_role,
            created_after=created_after_for_role,
            sessions_dir=agent_sessions_dir,
            match_cwd=sessions_dirs_by_role is None,
        )
        if not rollout_files:
            log.warning("No Codex rollout found for agent %s at %s", agent_role, wt_path)
            continue

        for idx, rollout_path in enumerate(rollout_files):
            log.info("Parsing Codex rollout %s for agent %s", rollout_path.name, agent_role)
            agent_output = _parse_codex_rollout(
                rollout_path,
                agent_role,
                catalog_tool_names,
                all_agent_roles,
                action_sink,
                expected_team_name=f"multi-agent-{run_id}" if run_id else None,
                sde_mcp_only=sde_mcp_only,
            )
            if agent_role == hub_role and idx == 0:
                final_text = agent_output

    return final_text
