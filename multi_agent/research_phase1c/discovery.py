"""P1 task-discovery evidence: live task-board filter matrix and schema-path checks.

The board is seeded exactly as the ClawTeam adapter seeds a real trial (team, sentinel,
one assignment per specialist created by ``seed_spoke_tasks`` with no explicit
priority). Each query is sent over real stdio MCP to both the vendored ``clawteam-mcp``
server and the TARGET_V1R4 contract server, and compared with ground truth computed
directly from the task files. CPU only; no model is involved.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import sys
import tempfile
import threading
import uuid
from pathlib import Path
from unittest.mock import patch

from .common import HUB, R4_RELEASE, REPO, read, rel, write

QUERIES = [
    ("team_name only", {}),
    ("status=pending", {"status": "pending"}),
    ("priority=high", {"priority": "high"}),
    ("priority=medium", {"priority": "medium"}),
    ("owner=risk_officer", {"owner": "risk_officer"}),
    ("owner=risk_officer, priority=high", {"owner": "risk_officer", "priority": "high"}),
    ("owner=risk_officer, priority=medium", {"owner": "risk_officer", "priority": "medium"}),
    (
        "R4 coordinator first call",
        {
            "status": "pending",
            "owner": "risk_officer",
            "priority": "high",
            "sort_by_priority": True,
        },
    ),
    ("pending + medium (no owner)", {"status": "pending", "priority": "medium"}),
    ("unknown owner", {"owner": "portfolio_manager"}),
    ("comma-joined owners", {"owner": "risk_officer,trader,compliance_analyst"}),
    ("unsupported status", {"status": "waiting_for_input"}),
    ("unsupported priority", {"priority": "critical"}),
]


_SEED_LOCK = threading.Lock()


def environ_snapshot() -> dict:
    """Copy of os.environ taken while no board seeding is patching it.

    seed_board temporarily patches os.environ; a concurrent subprocess spawn that
    inherits the live environment can then fail (EFAULT) or pick up another session's
    data directory. Every subprocess in the isolated runners gets an explicit snapshot.
    """
    with _SEED_LOCK:
        return dict(os.environ)


BUILDERS = {
    "r4": "multi_agent.research_phase1r.target.seed_assignments (TARGET_V1R4 code path: "
    "qualified tool names + protected task context with authorized ids)",
    "vanilla": "multi_agent.frameworks.clawteam.tasks.seed_spoke_tasks WITHOUT the R4 patch "
    "(DEFECT for isolated diagnostics: omits the protected task context and ids; used by "
    "the original T2/T3 runs; kept only to reproduce and document that defect)",
}


def seed_board(data_dir: Path, team_name: str, task, builder: str = "r4") -> dict:
    """Create the team, sentinel and specialist assignments as the target does.

    builder="r4" seeds through the exact TARGET_V1R4 assignment code
    (``research_phase1r.target.seed_assignments``), which appends the protected task
    context derived from the task contract and refuses to return until every stored
    assignment contains the authorized identifiers.

    ClawTeam reads CLAWTEAM_DATA_DIR from the process environment, so concurrent
    callers must not overlap (a race would seed a board into another session's folder).
    """
    from clawteam.store.file import FileTaskStore
    from clawteam.team.manager import TeamManager

    from multi_agent.frameworks.clawteam.adapter import SENTINEL_SUBJECT
    from multi_agent.frameworks.clawteam.tasks import seed_spoke_tasks

    if builder not in BUILDERS:
        raise ValueError(f"Unknown assignment builder {builder!r}")
    if builder == "r4":
        from multi_agent.research_phase1r.target import seed_assignments as seed_spoke_tasks

    with _SEED_LOCK, patch.dict(os.environ, {"CLAWTEAM_DATA_DIR": str(data_dir)}):
        hub = task.agents[0]
        TeamManager.create_team(
            name=team_name,
            leader_name=hub.role,
            leader_id=uuid.uuid4().hex[:12],
            description=f"phase1c board - {task.task_id}",
        )
        store = FileTaskStore(team_name)
        sentinel = store.create(
            subject=SENTINEL_SUBJECT,
            description="Hub must mark this completed after producing final output.",
            owner=hub.role,
        )
        seeded = seed_spoke_tasks(
            store, task.agents[1:], task=task, hub_role=hub.role, team_name=team_name
        )
        for agent in task.agents[1:]:
            TeamManager.add_member(
                team_name,
                agent.role,
                uuid.uuid4().hex[:12],
                agent_type=agent.description or "specialist",
            )
        tasks = [t.model_dump(mode="json") for t in store.list_tasks()]
    return dict(
        team_name=team_name,
        sentinel_id=sentinel.id,
        seeded=seeded,
        tasks=tasks,
        assignment_builder=builder,
    )


def ground_truth(tasks: list, args: dict):
    """Exact-equality AND semantics, as implemented by FileTaskStore._list_tasks_unlocked."""
    statuses = {"pending", "in_progress", "completed", "blocked"}
    priorities = {"low", "medium", "high", "urgent"}
    if args.get("status") and args["status"] not in statuses:
        return "INVALID_ENUM"
    if args.get("priority") and args["priority"] not in priorities:
        return "INVALID_ENUM"
    out = []
    for t in tasks:
        if args.get("status") and t["status"] != args["status"]:
            continue
        if args.get("owner") and t["owner"] != args["owner"]:
            continue
        if args.get("priority") and t["priority"] != args["priority"]:
            continue
        out.append(t["id"])
    return sorted(out)


def _server_params(kind: str, data_dir: Path):
    from mcp import StdioServerParameters

    env = dict(os.environ, CLAWTEAM_DATA_DIR=str(data_dir), PYTHONPATH=str(REPO))
    if kind == "vendored":
        cmd = shutil.which("clawteam-mcp") or str(Path(sys.executable).parent / "clawteam-mcp")
        return StdioServerParameters(command=cmd, env=env)
    return StdioServerParameters(
        command=sys.executable, args=["-m", "multi_agent.research_phase1r.tool_contract"], env=env
    )


async def _run_queries(kind, data_dir, team_name):
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    rows = []
    async with contextlib.AsyncExitStack() as stack:
        r, w = await stack.enter_async_context(stdio_client(_server_params(kind, data_dir)))
        s = await stack.enter_async_context(ClientSession(r, w))
        await s.initialize()
        listed = {
            t.name: t.model_dump(mode="json", exclude_none=True)
            for t in (await s.list_tools()).tools
        }
        for label, args in QUERIES:
            resp = await s.call_tool("task_list", dict(team_name=team_name, **args))
            text = "".join(getattr(b, "text", "") for b in resp.content)
            ids = None
            if not resp.isError:
                data = resp.structuredContent or json.loads(text)
                items = data.get("result") if isinstance(data, dict) else data
                ids = sorted(i["id"] for i in items)
            rows.append(
                dict(
                    query=label,
                    args=args,
                    is_error=resp.isError,
                    returned_ids=ids,
                    error_text=text[:300] if resp.isError else None,
                )
            )
        # A wrong team name, as typed by agents in 38 R4 specialist calls.
        wrong = team_name.replace("-8B-", "-")
        resp = await s.call_tool("task_list", dict(team_name=wrong))
        rows.append(
            dict(
                query="team name without '8B'",
                args={"team_name": wrong},
                is_error=resp.isError,
                returned_ids=None,
                error_text="".join(getattr(b, "text", "") for b in resp.content)[:300],
            )
        )
        overview = {}
        for tool in ("board_team", "task_stats"):
            if tool in listed:
                resp = await s.call_tool(tool, dict(team_name=team_name))
                overview[tool] = dict(
                    is_error=resp.isError,
                    text="".join(getattr(b, "text", "") for b in resp.content)[:1200],
                )
    return dict(tools=listed, rows=rows, overview=overview)


def filter_matrix(output: Path | None = None) -> dict:
    from multi_agent.research_phase1r.controls import task as load_task

    task = load_task()
    with tempfile.TemporaryDirectory(prefix="phase1c-board-") as tmp:
        data_dir = Path(tmp)
        team = f"multi-agent-ct-qwen_code-Qwen-Qwen3-8B-{uuid.uuid4().hex[:8]}"
        board = seed_board(data_dir, team, task)
        results = {
            kind: asyncio.run(_run_queries(kind, data_dir, team)) for kind in ("vendored", "v1r4")
        }
    owner_of = {t["id"]: t["owner"] for t in board["tasks"]}
    matrix, all_ok = [], True
    for i, (label, args) in enumerate(QUERIES):
        truth = ground_truth(board["tasks"], args)
        row = dict(
            query=label,
            args=args,
            ground_truth=truth,
            ground_truth_owners=None
            if truth == "INVALID_ENUM"
            else sorted(owner_of[x] for x in truth),
        )
        for kind in ("vendored", "v1r4"):
            r = results[kind]["rows"][i]
            ok = (r["is_error"] and truth == "INVALID_ENUM") or (
                not r["is_error"] and r["returned_ids"] == truth
            )
            all_ok &= ok
            row[kind] = dict(
                is_error=r["is_error"],
                returned_ids=r["returned_ids"],
                error_text=r["error_text"],
                matches_ground_truth=ok,
            )
        row["empty_but_board_nonempty"] = truth == [] and bool(board["tasks"])
        matrix.append(row)
    for kind in ("vendored", "v1r4"):
        wrong = results[kind]["rows"][-1]
        matrix_extra = dict(
            query=wrong["query"], is_error=wrong["is_error"], error_text=wrong["error_text"]
        )
        results[kind]["wrong_team"] = matrix_extra
    tl = {k: results[k]["tools"]["task_list"] for k in results}
    out = dict(
        method=(
            "Board seeded with the adapter's own code path; queries over real stdio MCP; "
            "ground truth computed from task files with exact-equality AND filters."
        ),
        board=dict(
            team_name="<random per run>",
            tasks=[
                {k: t[k] for k in ("id", "owner", "status", "priority", "subject")}
                for t in board["tasks"]
            ],
        ),
        priority_source=(
            "FileTaskStore.create(priority=None) -> TaskPriority.medium "
            "(vendor/clawteam/clawteam/store/file.py); seed_spoke_tasks passes "
            "no priority (multi_agent/frameworks/clawteam/tasks.py)"
        ),
        filter_semantics="exact string equality for status/owner/priority; filters combine "
        "with AND; enum values validated before filtering",
        matrix=matrix,
        all_results_match_ground_truth=all_ok,
        wrong_team_name={k: results[k]["wrong_team"] for k in results},
        board_overview_tools={k: results[k]["overview"] for k in results},
        task_list_schema=dict(
            vendored=tl["vendored"].get("inputSchema"),
            v1r4=tl["v1r4"].get("inputSchema"),
            v1r4_description=tl["v1r4"].get("description"),
            vendored_description=tl["vendored"].get("description"),
        ),
        model_visible_evidence=dict(
            qwen_code_capture=rel(R4_RELEASE / "schema_evidence"),
            served_registry_in_trial=(
                "results/research_phase1r/diagnostic_calibration/r4_5_per_condition_v1/trials/"
                "<trial>/instrumentation/coordination_tool_registry.json"
            ),
        ),
    )
    if output:
        write(output, out)
    return out


def cross_version_first_calls(audit_result: dict) -> list:
    """First coordinator call in every historical smoke (V1R..V1R3) and every R4 trial."""
    hist = read(R4_RELEASE / "cross_version_coordinator_ledgers.json", {}).get("trials", [])
    rows = [
        dict(
            source=f"smoke {h['smoke']}",
            trial=h["trial"],
            target=(h.get("target_identity") or "").split(":")[0],
            first_call=h["first_call"]["arguments"],
            priority_filter_present="priority" in h["first_call"]["arguments"],
        )
        for h in hist
    ]
    for t in audit_result["trials"]:
        c = t["coordinator"]
        if c.get("available") and c["first_calls"]:
            rows.append(
                dict(
                    source="R4 benign study",
                    trial=t["trial_id"],
                    target="TARGET_V1R4",
                    first_call=c["first_calls"][0]["args"],
                    priority_filter_present="priority" in c["first_calls"][0]["args"],
                )
            )
    return rows


def schema_priority_exposure() -> dict:
    """What the model saw about `priority` before (vendored) and after (V1R4)."""
    comp = read(R4_RELEASE / "schema_evidence/tool_schema_comparison.json", {})
    return dict(
        source=rel(R4_RELEASE / "schema_evidence/tool_schema_comparison.json"),
        changes=[c for c in comp.get("changes", []) if "task_list" in json.dumps(c)],
    )


__all__ = ["filter_matrix", "seed_board", "ground_truth", "cross_version_first_calls", "HUB"]
