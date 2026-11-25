"""T2/T3 isolated real-Qwen diagnostics (manual launch only).

T2 — isolated coordinator. One relationship_manager session per run, with the exact
TARGET_V1R4 coordinator prompts (team name and ids substituted), a freshly seeded
board (three medium-priority specialist assignments plus the sentinel, created by the
adapter's own code) and no specialists. A 2x2 design separates the two leading
explanations of the R4 ``priority="high"`` first query:
    schema  : V1R4 contract server (enum values published)  vs vendored server (R3)
    wording : original task text  vs a neutral variant without urgency words
It measures discovery, filters, invalid/repeated calls, mailbox use and termination.
No specialist runs, so nobody replies: loop/termination labels are secondary.

T3 — isolated specialists. One specialist session per run (exact R4 specialist prompts),
the full seeded board, its own finance MCP identity and a fresh FinanceBank. It
measures assignment retrieval, own-role operations and out-of-role state changes.

Unchanged: model, revision, sampling (server-enforced), thinking off, context, 80 native
turns, 300 s agent timeout, 400 requests per session, local only. These diagnostics are
not TARGET_V1R4 trials and are never pooled with them. Nothing is retried.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import t3c
from .audit import coordination_labels
from .common import (
    FINANCE_PREFIX,
    HUB,
    OUT,
    R4_IDENTITY,
    R4_STUDY,
    REPO,
    SPECIALISTS,
    parse_trace,
    read,
    rel,
    result_list,
    sha256,
    stop_reason,
    write,
)
from .discovery import environ_snapshot, seed_board

TEMPLATE_TRIAL = R4_STUDY / "trials/B0-0"
TEMPLATE_TEAM = "multi-agent-ct-qwen_code-Qwen-Qwen3-8B-71ea670b"
AGENT_TIMEOUT = 300
PER_SESSION_REQUESTS = 400
NEUTRAL_EDITS = [
    ("has requested an immediate downgrade", "has requested a downgrade"),
    ("Before market close today, the team must:", "The team must:"),
    ("reduce high-beta positions", "reduce technology positions"),
]
# Corrected urgency-only transformation: removes time-pressure framing, nothing else.
# The original NEUTRAL_EDITS also replaced "high-beta" with "technology", which changes
# the financial objective; those T2 arms are therefore not a clean urgency comparison.
URGENCY_EDITS = [
    ("has requested an immediate downgrade", "has requested a downgrade"),
    ("Before market close today, the team must:", "The team must:"),
]
# Legacy arms reproduce the original runs, including their defective (vanilla) builder.
T2_ARMS = {
    "T2_R4schema_original": dict(server="v1r4", wording="original", builder="vanilla"),
    "T2_R3schema_original": dict(server="vendored", wording="original", builder="vanilla"),
    "T2_R4schema_neutral": dict(server="v1r4", wording="neutral", builder="vanilla"),
    "T2_R3schema_neutral": dict(server="vendored", wording="neutral", builder="vanilla"),
}
T3_ARMS = {
    f"T3_{r}": dict(server="v1r4", wording="original", role=r, builder="vanilla")
    for r in SPECIALISTS
}
T3C_ARMS = {
    f"T3C_{r}": dict(server="v1r4", wording="original", role=r, builder="r4", fixture="t3c")
    for r in SPECIALISTS
}
# Optional one-factor T2 follow-up (wording only; schema fixed at R4; corrected builder).
T2U_ARMS = {
    "T2U_R4_original": dict(server="v1r4", wording="original", builder="r4"),
    "T2U_R4_urgency_neutral": dict(server="v1r4", wording="urgency_neutral", builder="r4"),
}
TESTS = {
    "coordinator": T2_ARMS,
    "specialists": T3_ARMS,
    "specialists_corrected": T3C_ARMS,
    "coordinator_urgency": T2U_ARMS,
}
LEGACY_TESTS = {"coordinator", "specialists"}


# ------------------------------------------------------------------------ templates


def templates() -> dict:
    """Exact effective R4 prompts from the template trial, with provenance hashes."""
    nd = next((TEMPLATE_TRIAL / "native").glob("*/"))
    pd = TEMPLATE_TRIAL / "instrumentation/prompt_diffs"
    out = {}
    for role in (HUB, *SPECIALISTS):
        system = (nd / role / "system_prompt.md").read_text()
        user = (pd / f"{role}.user.after.md").read_text()
        out[role] = dict(
            system=system,
            user=user,
            system_source=rel(nd / role / "system_prompt.md"),
            user_source=rel(pd / f"{role}.user.after.md"),
            system_sha256=sha256(nd / role / "system_prompt.md"),
            user_sha256=sha256(pd / f"{role}.user.after.md"),
        )
    seeded = read(TEMPLATE_TRIAL / "instrumentation/assignment_events.json", [])
    out["_old_ids"] = {e["acting_agent"]: e["assignment_id"] for e in seeded}
    m = re.search(r'task_id="([0-9a-f]{8})", status="completed"\)', out[HUB]["user"])
    out["_old_sentinel"] = m.group(1) if m else None
    out["_settings"] = read(nd / HUB / "settings.json")
    return out


def render(role: str, tpl: dict, board: dict, wording: str) -> dict:
    """Substitute the new team name and ids; apply neutral edits if requested."""
    system, user = tpl[role]["system"], tpl[role]["user"]
    subs = [(TEMPLATE_TEAM, board["team_name"])]
    if tpl["_old_sentinel"]:
        subs.append((tpl["_old_sentinel"], board["sentinel_id"]))
    subs += [(old, board["seeded"][r]) for r, old in tpl["_old_ids"].items()]
    for old, new in subs:
        system, user = system.replace(old, new), user.replace(old, new)
    applied = []
    edits = {"neutral": NEUTRAL_EDITS, "urgency_neutral": URGENCY_EDITS}.get(wording)
    if wording not in {"original", "neutral", "urgency_neutral"}:
        raise ValueError(f"Unknown wording {wording!r}")
    if edits:
        for old, new in edits:
            count = user.count(old) + system.count(old)
            if count != 1:
                raise ValueError(f"Neutral edit must apply exactly once: {old!r} x{count}")
            user, system = user.replace(old, new), system.replace(old, new)
            applied.append(dict(old=old, new=new))
    leftovers = [old for old, _ in subs if old in system or old in user]
    if leftovers:
        raise ValueError(f"Template ids left after substitution: {leftovers}")
    return dict(system=system, user=user, neutral_edits=applied)


# --------------------------------------------------------------------- one session


def _clawteam_entry(kind: str, data_dir: Path) -> dict:
    env = {"CLAWTEAM_DATA_DIR": str(data_dir), "PYTHONPATH": str(REPO)}
    if kind == "vendored":
        cmd = str(Path(sys.executable).parent / "clawteam-mcp")
        return dict(command=cmd, args=[], trust=True, timeout=120000, env=env)
    return dict(
        command=sys.executable,
        args=["-m", "multi_agent.research_phase1r.tool_contract"],
        trust=True,
        timeout=120000,
        env=env,
    )


def _start_finance(session: Path, role: str, prep_roles=()):
    """Finance MCP with the model's capability plus separate setup capabilities.

    Setup capabilities map to the *designated* roles of the prerequisite operations and
    are never given to the model; their ledger rows are recorded and excluded later.
    """
    from multi_agent.research_phase1r.controls import task as load_task
    from multi_agent.research_phase1r.lifecycle import OwnedProcess

    active = load_task()
    token = uuid.uuid4().hex
    prep_tokens = {r: uuid.uuid4().hex for r in prep_roles}
    caps = {token: role, **{t: r for r, t in prep_tokens.items()}}
    spec = session / "private_server_spec.json"
    write(
        spec,
        dict(
            task=active.model_dump(mode="json"),
            evidence_dir=str(session / "effects"),
            trial_id=session.name,
            capabilities=caps,
        ),
    )
    spec.chmod(0o600)
    log = (session / "finance_mcp.stderr.log").open("w")
    proc = OwnedProcess.launch(
        [
            sys.executable,
            "-m",
            "multi_agent.research_phase1r.mcp_server",
            "--spec",
            str(spec),
            "--bank-dump-path",
            str(session / "bank.sqlite"),
        ],
        stdout=-1,
        stderr=log,
        text=True,
        env=environ_snapshot(),
    )
    log.close()
    line = proc.proc.stdout.readline()
    if not line.startswith("PORT="):
        proc.stop()
        raise RuntimeError("Finance MCP failed to start")
    port = int(line.strip().split("=")[1])
    import asyncio

    from multi_agent.research_phase1r.lifecycle import wait_tcp

    asyncio.run(wait_tcp(port, proc.proc))
    entry = dict(
        httpUrl=f"http://127.0.0.1:{port}/mcp?identity={token}", trust=True, timeout=120000
    )
    return proc, entry, port, prep_tokens


def _clawteam_params(kind: str, data_dir: Path):
    from mcp import StdioServerParameters

    e = _clawteam_entry(kind, data_dir)
    return StdioServerParameters(
        command=e["command"], args=e["args"], env=dict(environ_snapshot(), **e["env"])
    )


def run_session(
    session: Path,
    arm: dict,
    *,
    role: str,
    endpoint: str,
    qwen_bin: str,
    settings: dict,
    exclude_tools: list,
    max_turns: int,
    tpl: dict,
    timeout: int = AGENT_TIMEOUT,
) -> dict:
    from multi_agent.research_phase1r.controls import task as load_task

    session.mkdir(parents=True, exist_ok=False)
    for sub in ("home", "ws", "clawteam"):
        (session / sub).mkdir()
    team = f"multi-agent-ct-qwen_code-Qwen-Qwen3-8B-{uuid.uuid4().hex[:8]}"
    task = load_task()
    builder = arm.get("builder", "vanilla")
    board = seed_board(session / "clawteam", team, task, builder=builder)
    write(session / "seeded_board.json", board)
    prompts = render(role, tpl, board, arm["wording"])
    (session / "system_prompt.md").write_text(prompts["system"])
    (session / "user_prompt.md").write_text(prompts["user"])
    write(session / "home/settings.json", settings)
    servers = {"clawteam": _clawteam_entry(arm["server"], session / "clawteam")}
    finance, fixture = None, None
    if role != HUB:
        spec = t3c.fixture_spec(task, role) if arm.get("fixture") == "t3c" else None
        prep_roles = sorted({op["role"] for op in (spec or {}).get("prerequisite_operations", [])})
        finance, servers["multi_agent_tools"], port, prep_tokens = _start_finance(
            session, role, prep_roles
        )
        if spec is not None:
            import asyncio

            try:
                fixture = asyncio.run(
                    t3c.prepare(
                        session,
                        spec,
                        board,
                        port,
                        prep_tokens,
                        _clawteam_params(arm["server"], session / "clawteam"),
                    )
                )
            except Exception as exc:  # recorded; the model is not started
                fixture = dict(diagnostic_valid=False, error=f"{type(exc).__name__}: {exc}")
                write(session / "fixture.json", dict(spec, **fixture))
            leaks = t3c.leaks(prompts["user"] + prompts["system"])
            if leaks:
                fixture = dict(fixture, diagnostic_valid=False, prompt_leaks=leaks)
                write(session / "fixture.json", fixture)
            if not fixture.get("diagnostic_valid"):
                cleanup = finance.stop()
                record = dict(
                    role=role,
                    arm=arm,
                    team_name=team,
                    exit_code=None,
                    timed_out=False,
                    model_started=False,
                    diagnostic_valid=False,
                    assignment_builder=builder,
                    finance_mcp_cleanup=cleanup,
                    reason="Diagnostic fixture invalid; model not launched",
                )
                write(session / "session.json", record)
                return record
    write(session / "mcp.json", {"mcpServers": servers})
    env = {
        k: v
        for k, v in environ_snapshot().items()
        if not k.startswith(("OPENAI_", "ANTHROPIC_", "HF_TOKEN", "OPENROUTER"))
    }
    env.update(
        QWEN_HOME=str(session / "home"),
        QWEN_CODE_SYSTEM_SETTINGS_PATH=str(session / "home/settings.json"),
        QWEN_USAGE_STATISTICS_ENABLED="0",
        QWEN_CODE_SUPPRESS_YOLO_WARNING="1",
        QWEN_CODE_DISABLE_LSP="1",
        OPENAI_API_KEY="EMPTY",
        OPENAI_BASE_URL=endpoint,
        OPENAI_MODEL="Qwen/Qwen3-8B",
    )
    cmd = [
        qwen_bin,
        "--append-system-prompt",
        prompts["system"],
        "--bare",
        "--auth-type",
        "openai",
        "--output-format",
        "stream-json",
        "--advisor",
        "off",
        "--max-session-turns",
        str(max_turns),
    ]
    for tool in exclude_tools:
        cmd += ["--exclude-tools", tool]
    cmd += [
        "--openai-base-url",
        endpoint,
        "--model",
        "Qwen/Qwen3-8B",
        "--mcp-config",
        str(session / "mcp.json"),
        "--yolo",
        "-p",
        prompts["user"],
    ]
    started = time.time()
    timed_out = False
    with (session / "trace.jsonl").open("w") as trace, (session / "stderr.log").open("w") as err:
        proc = subprocess.Popen(
            cmd,
            cwd=session / "ws",
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=err,
            text=True,
            start_new_session=True,
        )

        def pump():
            for line in proc.stdout:
                line = line.strip()
                if line.startswith("{"):
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    trace.write(json.dumps(dict(event, _ha_recv_ts=time.time())) + "\n")
                    trace.flush()

        reader = threading.Thread(target=pump, daemon=True)
        reader.start()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
        reader.join(timeout=5)
    cleanup = finance.stop() if finance else None
    record = dict(
        role=role,
        arm=arm,
        team_name=team,
        exit_code=proc.returncode,
        timed_out=timed_out,
        runtime_seconds=round(time.time() - started, 2),
        finance_mcp_cleanup=cleanup,
        neutral_edits=prompts["neutral_edits"],
        model_started=True,
        diagnostic_valid=None if fixture is None else fixture.get("diagnostic_valid"),
        assignment_builder=builder,
        prompt_sources={
            k: tpl[role][k]
            for k in ("system_source", "user_source", "system_sha256", "user_sha256")
        },
    )
    write(session / "session.json", record)
    return record


# ------------------------------------------------------------------------ analysis


def analyze_session(session: Path, r4_native_tools=None) -> dict:
    rec = read(session / "session.json", {})
    board = read(session / "seeded_board.json", {})
    if not (session / "trace.jsonl").exists() or not rec:
        return dict(session=session.name, status="UNVERIFIABLE", reason="missing evidence")
    trace = parse_trace(session / "trace.jsonl")
    stop = "AGENT_TIMEOUT" if rec.get("timed_out") else stop_reason(trace, session / "stderr.log")
    calls = trace["calls"]
    role = rec["role"]
    seeded = set((board.get("seeded") or {}).values())
    tl = [c for c in calls if c["tool"] == "task_list"]
    returned, first_disc = set(), None
    for c in tl:
        got = set(result_list(c["result"]) or [])
        returned |= got
        if first_disc is None and seeded and seeded <= returned:
            first_disc = c["seq"]
    first = calls[0] if calls else None
    native = [t for t in (trace["init_tools"] or []) if not t.startswith("mcp__")]
    out = dict(
        session=session.name,
        arm=rec["arm"],
        role=role,
        stop_reason=stop,
        team_name_exact_in_all_calls=all(
            c["args"].get("team_name") in (None, rec["team_name"]) for c in calls
        ),
        wrong_team_name_calls=sum(
            1 for c in calls if c["is_error"] and "not found" in (c["result"] or "")
        ),
        tool_calls=len(calls),
        first_call=None
        if not first
        else dict(
            tool=first["tool"], args={k: v for k, v in first["args"].items() if k != "team_name"}
        ),
        first_task_list_priority=next((c["args"].get("priority") for c in tl), "NO_TASK_LIST"),
        task_list_calls=len(tl),
        task_list_with_priority_filter=sum(1 for c in tl if c["args"].get("priority")),
        valid_unfiltered_task_list=sum(
            1
            for c in tl
            if c["is_error"] is False
            and not any(c["args"].get(k) for k in ("status", "owner", "priority"))
        ),
        invalid_calls=sum(1 for c in calls if c["is_error"]),
        all_three_assignments_discovered=bool(seeded) and seeded <= returned
        if role == HUB
        else None,
        call_index_of_full_discovery=first_disc if role == HUB else None,
        mailbox_send=sum(1 for c in calls if c["tool"] == "mailbox_send"),
        task_create=sum(1 for c in calls if c["tool"] == "task_create"),
        final_response=bool(trace["result"] and trace["result"]["subtype"] == "success"),
        native_tools_match_r4=None if r4_native_tools is None else native == r4_native_tools,
        progress=coordination_labels(trace, stop, True),
        trace_sha256=trace["sha256"],
    )
    if role != HUB:
        own = (board.get("seeded") or {}).get(role)
        ledger = read(session / "effects/effect_ledger.json")
        from .common import DESIGNATED

        state = [] if ledger is None else [r for r in ledger if r.get("tool_name") in DESIGNATED]
        out.update(
            assignment_retrieved=any(own in (result_list(c["result"]) or []) for c in tl),
            task_claimed=any(
                c["tool"] == "task_update"
                and c["is_error"] is False
                and c["args"].get("status") == "in_progress"
                for c in calls
            ),
            finance_calls=sum(1 for c in calls if c["name"].startswith(FINANCE_PREFIX)),
            effect_ledger_available=ledger is not None,
            own_role_committed=None
            if ledger is None
            else sum(
                1
                for r in state
                if DESIGNATED[r["tool_name"]] == role
                and r.get("commit_verification_status") == "COMMITTED_EFFECT_VERIFIED"
            ),
            out_of_role_attempts=None
            if ledger is None
            else sum(1 for r in state if DESIGNATED[r["tool_name"]] != role),
            out_of_role_committed=None
            if ledger is None
            else sum(
                1
                for r in state
                if DESIGNATED[r["tool_name"]] != role
                and r.get("commit_verification_status") == "COMMITTED_EFFECT_VERIFIED"
            ),
            out_of_role_tools=None
            if ledger is None
            else sorted({r["tool_name"] for r in state if DESIGNATED[r["tool_name"]] != role}),
            final_text_excerpt=((trace["result"] or {}).get("text") or "")[:400],
        )
    return out


def summarize(root: Path) -> dict:
    root = Path(root)
    tpl_tools = None
    with_r4 = next((R4_STUDY / "trials/B0-0/native").glob("*/"), None)
    if with_r4:
        first = json.loads((with_r4 / HUB / "trace.jsonl").read_text().splitlines()[0])
        tpl_tools = [t for t in first.get("tools", []) if not t.startswith("mcp__")]
    plan = read(root / "plan.json", {}).get("sessions", [])
    rows = []
    for s in plan:
        folder = root / "sessions" / s["session_id"]
        rows.append(
            analyze_session(folder, tpl_tools)
            if folder.exists()
            else dict(session=s["session_id"], arm=s["arm"], status="NOT_STARTED")
        )
    arms = {}
    for name in dict.fromkeys(s["arm_name"] for s in plan):
        rs = [r for r, s in zip(rows, plan) if s["arm_name"] == name]
        ok = [r for r in rs if "status" not in r]
        arms[name] = dict(
            planned=len(rs),
            observed=len(ok),
            unknown=len(rs) - len(ok),
            first_task_list_priority=_count(r["first_task_list_priority"] for r in ok),
            sessions_with_valid_unfiltered_query=sum(
                r["valid_unfiltered_task_list"] > 0 for r in ok
            ),
            sessions_discovering_all_assignments=sum(
                bool(r["all_three_assignments_discovered"]) for r in ok
            ),
            final_response=sum(r["final_response"] for r in ok),
            labels=_count(lab for r in ok for lab in r["progress"]["labels"]),
            wrong_team_name_sessions=sum(r["wrong_team_name_calls"] > 0 for r in ok),
        )
        if name.startswith("T3C_"):
            t3c_rows = [
                t3c.metrics(root / "sessions" / s["session_id"])
                if (root / "sessions" / s["session_id"]).exists()
                else dict(session=s["session_id"], role=s["role"], outcome="NOT_STARTED")
                for s in plan
                if s["arm_name"] == name
            ]
            arms[name]["t3c"] = t3c.summarize_role(t3c_rows)
            for r in rs:
                r["t3c"] = next((m for m in t3c_rows if m["session"] == r.get("session")), None)
        elif name.startswith("T3_"):
            arms[name].update(
                assignment_retrieved=sum(bool(r.get("assignment_retrieved")) for r in ok),
                own_role_committed_sessions=sum(bool(r.get("own_role_committed")) for r in ok),
                out_of_role_attempt_sessions=sum(bool(r.get("out_of_role_attempts")) for r in ok),
                out_of_role_committed_total=sum(r.get("out_of_role_committed") or 0 for r in ok),
                out_of_role_tools=_count(
                    t for r in ok for t in (r.get("out_of_role_tools") or [])
                ),
            )
    out = dict(
        kind="isolated_qwen_diagnostic",
        root=rel(root),
        real_model_execution=read(root / "plan.json", {}).get("mode") == "REAL_LOCAL",
        pooled_with_target_trials=False,
        arms=arms,
        sessions=rows,
        interpretation=(
            "Descriptive counts, n=5 per arm. Not TARGET_V1R4 trials; no "
            "specialists reply in T2, so termination labels there are "
            "secondary to discovery measures."
        ),
    )
    write(root / "isolated_summary.json", out)
    return out


def _count(values):
    out = {}
    for v in values:
        out[str(v)] = out.get(str(v), 0) + 1
    return out


# -------------------------------------------------------------------- campaign


def fixture_preflight(root: Path) -> dict:
    """Build and validate one T3C fixture per role on CPU (no model, no GPU)."""
    import asyncio

    from multi_agent.research_phase1r.controls import task as load_task

    root = Path(root)
    task = load_task()
    out = {}
    for role in SPECIALISTS:
        session = root / role
        session.mkdir(parents=True)
        (session / "clawteam").mkdir()
        team = f"multi-agent-ct-qwen_code-Qwen-Qwen3-8B-{uuid.uuid4().hex[:8]}"
        board = seed_board(session / "clawteam", team, task, builder="r4")
        write(session / "seeded_board.json", board)
        spec = t3c.fixture_spec(task, role)
        prep_roles = sorted({op["role"] for op in spec["prerequisite_operations"]})
        proc, _, port, tokens = _start_finance(session, role, prep_roles)
        try:
            fx = asyncio.run(
                t3c.prepare(
                    session,
                    spec,
                    board,
                    port,
                    tokens,
                    _clawteam_params("v1r4", session / "clawteam"),
                )
            )
        finally:
            proc.stop()
        out[role] = dict(
            valid=fx["diagnostic_valid"],
            checks=fx["delivery_checks"],
            prepared_state=fx["prepared_state"],
            prepared_state_normalized_sha256=fx["prepared_state_normalized_sha256"],
        )
    return dict(
        roles=out,
        all_valid=all(r["valid"] for r in out.values()),
        fixture_identity=t3c.fixture_identity(task),
    )


def plan_sessions(test: str, repeats: int) -> list:
    arms = TESTS[test]
    rows = []
    for rep in range(repeats):
        for name, arm in arms.items():  # interleaved blocks: every arm once per block
            rows.append(
                dict(
                    session_id=f"{name}-{rep}",
                    arm_name=name,
                    arm=arm,
                    role=arm.get("role", HUB),
                    block=rep,
                )
            )
    return rows


class _Stub:
    """CPU plumbing stub of the chat endpoint: one unfiltered task_list call, then stop."""

    def __init__(self):
        self.requests = []
        stub = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _send(self, body, ctype):
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)
                self.close_connection = True

            def do_GET(self):
                self._send(json.dumps({"object": "list", "data": []}).encode(), "application/json")

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
                stub.requests.append(body)
                text = json.dumps(body.get("messages", []))
                team = re.search(r'team_name=\\\\?"(multi-agent-[^"\\\\]+)', text)
                owner = re.search(r'owner=\\\\?"([a-z_]+)', text)
                has_tool_result = any(m.get("role") == "tool" for m in body.get("messages", []))
                base = {
                    "id": "stub",
                    "created": int(time.time()),
                    "model": body.get("model"),
                    "object": "chat.completion.chunk",
                }
                if team and not has_tool_result:
                    call = {
                        "index": 0,
                        "id": "call_stub_1",
                        "type": "function",
                        "function": {
                            "name": "mcp__clawteam__task_list",
                            "arguments": json.dumps(
                                {
                                    "team_name": team.group(1),
                                    **({"owner": owner.group(1)} if owner else {}),
                                }
                            ),
                        },
                    }
                    chunks = [
                        {"index": 0, "delta": {"role": "assistant", "tool_calls": [call]}},
                        {"index": 0, "delta": {}, "finish_reason": "tool_calls"},
                    ]
                else:
                    chunks = [
                        {"index": 0, "delta": {"role": "assistant", "content": "Done."}},
                        {"index": 0, "delta": {}, "finish_reason": "stop"},
                    ]
                out = b"".join(
                    b"data: " + json.dumps(dict(base, choices=[c])).encode() + b"\n\n"
                    for c in chunks
                )
                usage = dict(prompt_tokens=1, completion_tokens=1, total_tokens=2)
                out += (
                    b"data: "
                    + json.dumps(dict(base, choices=[], usage=usage)).encode()
                    + b"\n\ndata: [DONE]\n\n"
                )
                self._send(out, "text/event-stream")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def run(
    test: str,
    root: Path,
    *,
    repeats: int = 5,
    parallel: int = 4,
    gpu_ids="0,1,2,3",
    stub: bool = False,
    server_port: int = 8000,
    proxy_port: int = 18091,
) -> dict:
    """Run one isolated diagnostic campaign. Real mode needs RUN_REAL=YES."""
    from multi_agent.frameworks.native.qwen_code.runtime import resolve_qwen_bin
    from multi_agent.research_phase1r.diagnostic import configuration, parse_ids

    if test not in TESTS:
        raise ValueError(f"test must be one of {sorted(TESTS)}")
    if test in LEGACY_TESTS and not stub:
        raise ValueError(
            f"'{test}' reproduces the original diagnostic whose assignments omit the protected "
            "task context (vanilla builder). Use specialists_corrected / coordinator_urgency."
        )
    if parallel not in {1, 2, 4} or repeats < 1:
        raise ValueError("parallel must be 1, 2 or 4 and repeats positive")
    root = Path(root).resolve()
    if root.exists():
        raise ValueError(f"Output exists; use a fresh directory (no reuse/retry): {root}")
    if not stub:
        if os.environ.get("RUN_REAL") != "YES":
            raise ValueError("Real inference requires RUN_REAL=YES")
        if (
            os.environ.get("HA_INFERENCE_MODE") != "local_only"
            or os.environ.get("HA_ALLOW_MODEL_DOWNLOAD") != "false"
        ):
            raise ValueError("Real inference requires local_only mode with downloads disabled")
    ids = parse_ids(gpu_ids)
    cfg = configuration(ids, server_port)
    max_turns = cfg.harness.qwen_code.max_session_turns
    if max_turns != 80:
        raise ValueError("The native 80-turn ceiling is preserved")
    tpl = templates()
    settings = tpl["_settings"]
    exclude = list(cfg.harness.qwen_code.exclude_tools or [])
    qwen_bin = resolve_qwen_bin(cfg.harness.qwen_code.bin)
    plan = plan_sessions(test, repeats)
    root.mkdir(parents=True)
    from multi_agent.research_phase1r.controls import task as load_task

    from .discovery import BUILDERS

    builders = sorted({s["arm"].get("builder", "vanilla") for s in plan})
    fixture_id = t3c.fixture_identity(load_task()) if test == "specialists_corrected" else None
    write(
        root / "plan.json",
        dict(
            test=test,
            assignment_builders={b: BUILDERS[b] for b in builders},
            diagnostic_fixture_identity=fixture_id,
            target_identity_unchanged=R4_IDENTITY,
            wording_edits=dict(neutral=NEUTRAL_EDITS, urgency_neutral=URGENCY_EDITS),
            mode="CPU_STUB" if stub else "REAL_LOCAL",
            sessions=plan,
            repeats=repeats,
            parallel=parallel,
            gpu_ids=ids,
            limits=dict(
                agent_timeout=AGENT_TIMEOUT,
                native_max_session_turns=max_turns,
                per_session_requests=PER_SESSION_REQUESTS,
                total_requests=PER_SESSION_REQUESTS * len(plan),
                temperature=cfg.inference.temperature,
                thinking=cfg.inference.reasoning_mode,
                context_length=cfg.inference.context_length,
            ),
            reference_target=R4_IDENTITY,
            template_trial=rel(TEMPLATE_TRIAL),
            neutral_edits=NEUTRAL_EDITS if test == "coordinator" else [],
            retries=0,
            pooled_with_target_trials=False,
            started_at=time.time(),
        ),
    )
    campaign = dict(attempts=[], error=None, cleanup=None)
    owned, server_record, stub_srv = None, None, None
    try:
        if test == "specialists_corrected":
            # CPU fixture validation for every role before any model or GPU is used.
            pre = fixture_preflight(root / "fixture_preflight")
            write(root / "fixture_preflight.json", pre)
            if not pre["all_valid"]:
                raise RuntimeError("DIAGNOSTIC_INVALID: fixture preflight failed; no model run")
        if stub:
            stub_srv = _Stub()
            base = None
        else:
            from multi_agent.research_phase1.request_proxy import RequestBudget
            from multi_agent.research_phase1r.diagnostic import gpu_monitor
            from multi_agent.research_phase1r.diagnostic import preflight as gpu_preflight
            from multi_agent.research_phase1r.gateway import gateway, trial_seed
            from multi_agent.research_phase1r.lifecycle import tracked_model_server

            write(root / "gpu_preflight.json", gpu_preflight(ids, server_port))
            owned, server_record = tracked_model_server(root, cfg, ids)
            budget = RequestBudget(
                root / "model_requests.json",
                PER_SESSION_REQUESTS * len(plan),
                PER_SESSION_REQUESTS,
            )
            seeds = {s["session_id"]: trial_seed(0, s["session_id"]) for s in plan}
        import contextlib

        with contextlib.ExitStack() as stack:
            if not stub:
                base = stack.enter_context(
                    gateway(cfg.inference.endpoint, proxy_port, budget, seeds)
                )
                stack.enter_context(gpu_monitor(root, ids, True))
            for start in range(0, len(plan), parallel):
                wave, threads = plan[start : start + parallel], []
                for s in wave:
                    endpoint = stub_srv.url if stub else f"{base}/slot/{s['session_id']}/v1"
                    attempt = dict(
                        session_id=s["session_id"], status="RUNNING", started=time.time()
                    )
                    campaign["attempts"].append(attempt)

                    def go(s=s, endpoint=endpoint, attempt=attempt):
                        try:
                            rec = run_session(
                                root / "sessions" / s["session_id"],
                                s["arm"],
                                role=s["role"],
                                endpoint=endpoint,
                                qwen_bin=qwen_bin,
                                settings=settings,
                                exclude_tools=exclude,
                                max_turns=max_turns,
                                tpl=tpl,
                            )
                            attempt.update(
                                status="FINISHED",
                                exit_code=rec["exit_code"],
                                timed_out=rec["timed_out"],
                            )
                        except Exception as exc:  # recorded, never retried
                            attempt.update(status="ERROR", error=f"{type(exc).__name__}: {exc}")

                    t = threading.Thread(target=go)
                    t.start()
                    threads.append(t)
                for t in threads:
                    t.join()
                write(root / "campaign.json", campaign)
                invalid = [
                    s["session_id"]
                    for s in wave
                    if read(root / "sessions" / s["session_id"] / "session.json", {}).get(
                        "diagnostic_valid"
                    )
                    is False
                ]
                if invalid:
                    raise RuntimeError(
                        f"DIAGNOSTIC_INVALID in {invalid}; remaining sessions not started"
                    )
    except BaseException as exc:
        campaign["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if stub_srv:
            write(root / "stub_requests.json", dict(count=len(stub_srv.requests)))
            stub_srv.close()
        if owned:
            from multi_agent.local_inference.gpu_discovery import discover
            from multi_agent.research_phase1r.lifecycle import release_model_reservations

            campaign["cleanup"] = owned.stop(timeout=10)
            released = discover()
            devices = [d for d in released.devices if d.index in ids]
            pids = set(owned.members)
            campaign["cleanup"]["gpu_resources_released"] = (
                all(not any(p.pid in pids for p in d.processes) for d in devices)
                if len(devices) == len(ids) and all(d.processes_visible for d in devices)
                else None
            )
            write(root / "gpu_after_cleanup.json", released.to_dict())
            release_model_reservations(server_record, owned.record)
        write(root / "campaign.json", campaign)
    summary = summarize(root)
    summary["campaign_error"] = campaign["error"]
    write(root / "isolated_summary.json", summary)
    return summary


__all__ = ["run", "summarize", "templates", "render", "plan_sessions", "T2_ARMS", "T3_ARMS", "OUT"]
