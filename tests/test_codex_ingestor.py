from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from multi_agent.frameworks.native.codex.ingestor import (
    _find_codex_sessions,
    _parse_codex_rollout,
    collect_codex_traces,
)


class _RecordingSink:
    def __init__(self) -> None:
        self.tool_calls: list[dict[str, object]] = []
        self.communications: list[dict[str, object]] = []

    def emit_tool_call(self, **kwargs: object) -> None:
        self.tool_calls.append(kwargs)

    def emit_communication(self, **kwargs: object) -> None:
        self.communications.append(kwargs)


def _write_rollout(path: Path, *, cwd: Path, mtime_ns: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "type": "session_meta",
                "payload": {"cwd": str(cwd)},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    os.utime(path, ns=(mtime_ns, mtime_ns))


def test_isolated_sessions_do_not_require_rollout_cwd_match(tmp_path: Path) -> None:
    sessions_dir = tmp_path / "isolated-home" / "sessions"
    rollout = sessions_dir / "2026" / "09" / "22" / "rollout-agent.jsonl"
    _write_rollout(
        rollout,
        cwd=tmp_path / "codex-recorded-a-different-cwd",
        mtime_ns=2_000_000_000,
    )

    found = _find_codex_sessions(
        tmp_path / "clawteam-workspace",
        "concierge_lead",
        sessions_dir=sessions_dir,
        match_cwd=False,
    )

    assert found == [rollout]


def test_shared_sessions_still_match_by_cwd(tmp_path: Path) -> None:
    sessions_dir = tmp_path / "shared-sessions"
    agent_dir = tmp_path / "agent-workspace"
    matching = sessions_dir / "2026" / "09" / "22" / "rollout-matching.jsonl"
    unrelated = sessions_dir / "2026" / "09" / "22" / "rollout-unrelated.jsonl"
    _write_rollout(matching, cwd=agent_dir, mtime_ns=2_000_000_000)
    _write_rollout(unrelated, cwd=tmp_path / "other-agent", mtime_ns=3_000_000_000)

    found = _find_codex_sessions(
        agent_dir,
        "concierge_lead",
        sessions_dir=sessions_dir,
    )

    assert found == [matching]


def test_isolated_sessions_select_newest_rollout(tmp_path: Path) -> None:
    sessions_dir = tmp_path / "isolated-home" / "sessions"
    older = sessions_dir / "2026" / "09" / "22" / "rollout-older.jsonl"
    newer = sessions_dir / "2026" / "09" / "22" / "rollout-newer.jsonl"
    _write_rollout(older, cwd=tmp_path / "first", mtime_ns=2_000_000_000)
    _write_rollout(newer, cwd=tmp_path / "second", mtime_ns=3_000_000_000)

    found = _find_codex_sessions(
        tmp_path / "clawteam-workspace",
        "concierge_lead",
        sessions_dir=sessions_dir,
        match_cwd=False,
    )

    assert found == [newer]


def test_collection_uses_isolated_role_directory_as_identity(tmp_path: Path) -> None:
    sessions_dir = tmp_path / "isolated-home" / "sessions"
    rollout = sessions_dir / "2026" / "09" / "22" / "rollout-agent.jsonl"
    _write_rollout(
        rollout,
        cwd=tmp_path / "codex-recorded-a-different-cwd",
        mtime_ns=2_000_000_000,
    )
    with rollout.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "completed"}],
                    },
                }
            )
            + "\n"
        )

    output = collect_codex_traces(
        worktree_map={"concierge_lead": tmp_path / "clawteam-workspace"},
        catalog_tool_names=set(),
        all_agent_roles=["concierge_lead"],
        action_sink=object(),
        hub_role="concierge_lead",
        sessions_dirs_by_role={"concierge_lead": sessions_dir},
    )

    assert output == "completed"


def test_rollout_surfaces_native_codex_error(tmp_path: Path) -> None:
    rollout = tmp_path / "rollout-error.jsonl"
    rollout.write_text(
        json.dumps(
            {
                "type": "event_msg",
                "payload": {
                    "type": "task_complete",
                    "last_agent_message": None,
                    "error": {
                        "message": json.dumps(
                            {
                                "type": "error",
                                "status": 400,
                                "error": {
                                    "message": (
                                        "The 'gpt-5.4' model is not supported when using "
                                        "Codex with a ChatGPT account."
                                    )
                                },
                            }
                        )
                    },
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="gpt-5.4.*not supported"):
        _parse_codex_rollout(
            rollout,
            "concierge_lead",
            set(),
            ["concierge_lead"],
            object(),
        )


def test_parser_emits_current_codex_mcp_item_completed_event(tmp_path: Path) -> None:
    rollout = tmp_path / "rollout-mcp.jsonl"
    rollout.write_text(
        json.dumps(
            {
                "timestamp": "2026-09-23T03:36:02.283Z",
                "type": "event_msg",
                "payload": {
                    "type": "item_completed",
                    "item": {
                        "type": "McpToolCall",
                        "server": "multi_agent_tools",
                        "tool": "get_customer_context",
                        "arguments": {
                            "customer_id": "DL-CUST-004",
                            "context_scope": "wellness",
                        },
                        "status": "completed",
                        "result": {
                            "content": [{"type": "text", "text": "Customer Context"}],
                            "isError": False,
                        },
                    },
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    sink = _RecordingSink()

    _parse_codex_rollout(
        rollout,
        "booking_specialist",
        {"get_customer_context"},
        ["concierge_lead", "booking_specialist"],
        sink,
    )

    assert len(sink.tool_calls) == 1
    assert sink.tool_calls[0]["tool_name"] == "get_customer_context"
    assert sink.tool_calls[0]["tool_args"] == {
        "customer_id": "DL-CUST-004",
        "context_scope": "wellness",
    }
    assert sink.tool_calls[0]["tool_result"] == "Customer Context"
