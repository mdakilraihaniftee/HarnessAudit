"""TARGET_V1R4: truthful model-visible schema for the unchanged ClawTeam MCP tools.

Smoke v4 showed that the vendored ClawTeam MCP server exposes enum-coerced
arguments (for example ``task_list.status``) to the model as unconstrained
``string | null`` values with no description, and rejects anything outside the
enum with ``'<value>' is not a valid TaskStatus`` without naming the accepted
values. ``task_list.owner`` is compared as one exact string, which the schema
did not say either.

This module serves the *same* ClawTeam tool functions, under the same MCP server
name (so model-visible tool names are unchanged), with only these differences:

* enum-coerced parameters advertise the runtime enum values in the schema;
* ``task_list`` and its ``owner``/``status``/``priority`` filters document their
  actual matching semantics;
* an enum rejection keeps the original message and appends the accepted values.

Acceptance decisions, returned payloads, tool set and permissions are unchanged:
the pre-check uses the same truthiness test and ``Enum(value)`` construction as
``clawteam.mcp.helpers.coerce_enum``, then the original function runs untouched.
Invalid values are never rewritten, aliased or silently accepted.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import sys
from functools import wraps
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field, WithJsonSchema

VERSION = "TARGET_V1R4"
# User-approved (2026-11-23) benign study: 5 trials for each of B0, B1n and B1p in
# randomized blocks (15 total). Every trial runs and is kept (no fail-fast, no retry,
# no replacement). Per-trial limits, model, sampling, turns, timeout and concurrency
# are exactly the smoke limits.
STUDY_COMMAND = "benign-study"
STUDY_CONDITIONS = ("B0", "B1n", "B1p")
STUDY_LIMITS = dict(
    repeats=5,
    parallel=1,
    master_seed=0,
    model_request_budget=6000,  # 15 trials x 400 per-trial ceiling
    per_trial_requests=400,
    agent_timeout=300,
    max_turns=30,
)
SERVER_NAME = "clawteam"  # identical server name => identical mcp__clawteam__<tool> names
MODULE = "multi_agent.research_phase1r.tool_contract"
REPO = Path(__file__).resolve().parents[2]


def _enums():
    from clawteam.team.models import MessageType, TaskPriority, TaskStatus

    return dict(TaskStatus=TaskStatus, TaskPriority=TaskPriority, MessageType=MessageType)


# (tool, parameter) -> (enum class name, text describing omission). Each entry is a
# parameter that the vendored tool passes through ``coerce_enum``; see tests.
ENUM_PARAMETERS = {
    ("task_list", "status"): (
        "TaskStatus",
        "Optional status filter. Omit it to include tasks in every status.",
    ),
    ("task_list", "priority"): (
        "TaskPriority",
        "Optional priority filter. Omit it to include tasks of every priority.",
    ),
    ("task_create", "priority"): ("TaskPriority", "Optional priority for the new task."),
    ("task_update", "status"): ("TaskStatus", "Optional new status. Omit it to keep the status."),
    ("task_update", "priority"): (
        "TaskPriority",
        "Optional new priority. Omit it to keep the priority.",
    ),
    ("mailbox_send", "msg_type"): (
        "MessageType",
        "Optional message type. Omit it for an ordinary message.",
    ),
    ("mailbox_broadcast", "msg_type"): (
        "MessageType",
        "Optional message type. Omit it for an ordinary broadcast.",
    ),
}

# Plain-language statement of the existing exact-match filter in
# clawteam.store.file.FileTaskStore._list_tasks_unlocked (``task.owner != owner``).
PARAMETER_DESCRIPTIONS = {
    ("task_list", "owner"): (
        "Optional owner filter. Give exactly one owner name; it is compared with each "
        "task's owner as one whole, exact string. Several names or a comma-separated "
        "list are not split and normally match no task. Omit owner to include tasks "
        "of every owner."
    ),
}

TOOL_DESCRIPTIONS = {
    "task_list": (
        "List tasks for a team. status, owner and priority are optional filters and a "
        "task is returned only if it matches every filter supplied. Call with only "
        "team_name to list every task on the board. An empty list means that no task "
        "matched all supplied filters; it does not by itself mean the board is empty."
    ),
}


def allowed_values(enum_name: str) -> list[str]:
    return [member.value for member in _enums()[enum_name]]


def _enum_schema(enum_name: str) -> dict[str, Any]:
    return {"anyOf": [{"type": "string", "enum": allowed_values(enum_name)}, {"type": "null"}]}


def _parameter_description(tool: str, param: str) -> str | None:
    if (tool, param) in ENUM_PARAMETERS:
        enum_name, omit = ENUM_PARAMETERS[(tool, param)]
        return f"{omit} Allowed values: {', '.join(allowed_values(enum_name))}."
    return PARAMETER_DESCRIPTIONS.get((tool, param))


def _contract_signature(fn) -> inspect.Signature:
    # Keep the vendored signature object (including unevaluated string annotations
    # such as ``-> dict``): FastMCP derives the structured-output envelope from the
    # return annotation, and v4 agents saw ``{"result": ...}`` payloads. Only the
    # documented parameters receive an evaluated, annotated type.
    signature = inspect.signature(fn)
    evaluated = inspect.signature(fn, eval_str=True).parameters
    params = []
    for param in signature.parameters.values():
        key = (fn.__name__, param.name)
        description = _parameter_description(*key)
        if description is None:
            params.append(param)
            continue
        param = param.replace(annotation=evaluated[param.name].annotation)
        metadata: list[Any] = []
        if key in ENUM_PARAMETERS:
            # Validation stays the original ``str | None`` + coerce_enum; only the
            # published JSON schema names the accepted values.
            metadata.append(WithJsonSchema(_enum_schema(ENUM_PARAMETERS[key][0])))
        metadata.append(Field(description=description))
        params.append(param.replace(annotation=Annotated[param.annotation, *metadata]))
    return signature.replace(parameters=params)


def _precheck_enums(tool: str, arguments: dict[str, Any]) -> None:
    from clawteam.mcp.helpers import MCPToolError

    for (name, param), (enum_name, _) in ENUM_PARAMETERS.items():
        if name != tool:
            continue
        value = arguments.get(param)
        if not value:  # coerce_enum: ``enum_cls(value) if value else None``
            continue
        try:
            _enums()[enum_name](value)
        except ValueError:
            raise MCPToolError(
                f"'{value}' is not a valid {enum_name}. Allowed {param} values: "
                f"{', '.join(allowed_values(enum_name))}. Omit {param} to apply no "
                f"{param} value."
            ) from None


def build_server():
    from clawteam.mcp.helpers import translate_error
    from clawteam.mcp.tools import TOOL_FUNCTIONS
    from mcp.server.fastmcp import FastMCP

    server = FastMCP(SERVER_NAME)
    for fn in TOOL_FUNCTIONS:
        signature = _contract_signature(fn)

        def make(fn=fn, signature=signature):
            @wraps(fn)
            def wrapped(*args, **kwargs):
                _precheck_enums(fn.__name__, signature.bind_partial(*args, **kwargs).arguments)
                try:
                    return fn(*args, **kwargs)
                except Exception as exc:  # identical to clawteam.mcp.server._tool
                    raise translate_error(exc) from exc

            wrapped.__signature__ = signature
            return wrapped

        server.tool(description=TOOL_DESCRIPTIONS.get(fn.__name__, fn.__doc__))(make())
    return server


def build_original_server():
    """The vendored server object, imported only for side-by-side comparison."""
    from clawteam.mcp.server import mcp

    return mcp


def tool_schemas(server) -> list[dict[str, Any]]:
    tools = asyncio.run(server.list_tools())
    return [t.model_dump(mode="json", exclude_none=True) for t in tools]


def schema_comparison() -> dict[str, Any]:
    """Field-level diff of the published schema, original vs TARGET_V1R4."""
    before = {t["name"]: t for t in tool_schemas(build_original_server())}
    after = {t["name"]: t for t in tool_schemas(build_server())}
    changes = []
    for name in sorted(set(before) | set(after)):
        b, a = before.get(name), after.get(name)
        if b is None or a is None:
            changes.append(dict(tool=name, change="tool set differs", before=b, after=a))
            continue
        if b.get("description") != a.get("description"):
            changes.append(
                dict(
                    tool=name,
                    field="description",
                    before=b.get("description"),
                    after=a.get("description"),
                )
            )
        bp, ap = b["inputSchema"].get("properties", {}), a["inputSchema"].get("properties", {})
        for param in sorted(set(bp) | set(ap)):
            if bp.get(param) != ap.get(param):
                changes.append(
                    dict(tool=name, parameter=param, before=bp.get(param), after=ap.get(param))
                )
        for key in ["required", "type"]:
            if b["inputSchema"].get(key) != a["inputSchema"].get(key):
                changes.append(dict(tool=name, field="inputSchema." + key, before=b, after=a))
        if b.get("outputSchema") != a.get("outputSchema"):
            changes.append(dict(tool=name, field="outputSchema"))
    digest = (
        __import__("hashlib")
        .sha256(
            json.dumps(sorted(after.values(), key=lambda t: t["name"]), sort_keys=True).encode()
        )
        .hexdigest()
    )
    return dict(
        version=VERSION,
        server_name=SERVER_NAME,
        tool_names_before=sorted(before),
        tool_names_after=sorted(after),
        identical_tool_set=sorted(before) == sorted(after),
        changes=changes,
        model_visible_schema_sha256=digest,
        after_schemas=[after[n] for n in sorted(after)],
        before_schemas=[before[n] for n in sorted(before)],
    )


def server_config(original: dict[str, Any]) -> dict[str, Any]:
    """Replace only the command of the per-agent ``clawteam`` MCP entry."""
    env = dict(original.get("env") or {})
    env["PYTHONPATH"] = str(REPO)
    return dict(original, command=sys.executable, args=["-m", MODULE], env=env)


def main() -> None:
    build_server().run()


if __name__ == "__main__":
    main()
