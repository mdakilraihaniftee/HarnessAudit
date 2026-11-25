"""Paths, JSON helpers and an independent parser for raw Qwen Code stream-json traces."""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
R4_STUDY = REPO / "results/research_phase1r/diagnostic_calibration/r4_5_per_condition_v1"
R4_RELEASE = REPO / "results/research_phase1r/target_v1r4"
R4_AUDIT = REPO / "results/research_phase1r/r4_audit"
OUT = REPO / "results/research_phase1c"
R4_IDENTITY = "TARGET_V1R4:213a034dc8892db73dda01a606e6781965a6a3d0c4bb2d4cb3b7b66c66749cf8"
HUB = "relationship_manager"
SPECIALISTS = ("risk_officer", "trader", "compliance_analyst")
DESIGNATED = {
    "set_risk_limits": "risk_officer",
    "execute_trade": "trader",
    "submit_compliance_report": "compliance_analyst",
}
CLAWTEAM_PREFIX = "mcp__clawteam__"
FINANCE_PREFIX = "mcp__multi_agent_tools__"


def read(path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    return json.loads(path.read_text())


def write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True, default=str) + "\n")
    return path


def sha256(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def rel(path) -> str:
    path = Path(path).resolve()
    try:
        return str(path.relative_to(REPO))
    except ValueError:
        return str(path)


def refuse_inside(output, protected):
    """Derived outputs must never be written into evidence folders."""
    output, protected = Path(output).resolve(), Path(protected).resolve()
    if output == protected or protected in output.parents:
        raise ValueError(f"Refusing to write derived output inside evidence: {output}")


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    return json.dumps(content)


def parse_trace(path) -> dict:
    """Parse one raw stream-json trace without using any harness-derived artifact.

    Returns tool calls in emission order (with their matched results), the native
    result event if present and the line numbers that support each row.
    """
    path = Path(path)
    calls, by_id, result, init, malformed = [], {}, None, None, 0
    for lineno, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            init = event
        elif kind == "assistant":
            for block in (event.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    row = dict(
                        seq=len(calls) + 1,
                        id=block.get("id"),
                        name=block.get("name", ""),
                        tool=block.get("name", "").split("__")[-1],
                        server=(block.get("name", "").split("__") + ["", ""])[1]
                        if block.get("name", "").startswith("mcp__")
                        else "native",
                        args=block.get("input") or {},
                        ts=event.get("_ha_recv_ts"),
                        call_line=lineno,
                        result=None,
                        is_error=None,
                        result_line=None,
                        result_ts=None,
                    )
                    calls.append(row)
                    by_id[row["id"]] = row
        elif kind == "user":
            for block in (event.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    row = by_id.get(block.get("tool_use_id"))
                    if row is not None:
                        row.update(
                            result=_text(block.get("content")),
                            is_error=bool(block.get("is_error")),
                            result_line=lineno,
                            result_ts=event.get("_ha_recv_ts"),
                        )
        elif kind == "result":
            result = dict(
                subtype=event.get("subtype"),
                is_error=event.get("is_error"),
                num_turns=event.get("num_turns"),
                text=(event.get("result") or "")[:2000],
                line=lineno,
                ts=event.get("_ha_recv_ts"),
            )
    return dict(
        source=rel(path),
        sha256=sha256(path),
        calls=calls,
        result=result,
        init_tools=(init or {}).get("tools"),
        malformed_lines=malformed,
    )


def stop_reason(trace: dict, stderr_path) -> str:
    """Native stop reason from the result event, else from the CLI's stderr receipt."""
    if trace["result"]:
        return f"NATIVE_RESULT:{trace['result']['subtype']}"
    stderr = Path(stderr_path).read_text(errors="replace") if Path(stderr_path).exists() else ""
    if "Reached max session turns" in stderr:
        return "SESSION_TURN_LIMIT"
    if "Operation cancelled" in stderr:
        return "CANCELLED_NO_TERMINAL"
    return "NO_NATIVE_TERMINAL_UNKNOWN_CAUSE"


def result_list(text):
    """Task ids returned by a ClawTeam task_list result, or None if not a list result."""
    if not isinstance(text, str):
        return None
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        # The CLI may truncate long tool results; the visible prefix still shows which
        # task objects were returned (each item starts with its id).
        if text.lstrip().startswith('{"result":['):
            return re.findall(r'[\[,]\{"id":"([0-9a-zA-Z_-]+)"', text)
        return None
    items = data.get("result") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return None
    return [i.get("id") for i in items if isinstance(i, dict)]


def now():
    return time.time()
