"""Independent T2/T3 audit and corrected T2 interpretation (Tasks A, C/9).

Recomputes everything from raw session evidence (trace.jsonl, seeded_board.json,
effect ledgers, model_requests.json) and reconciles with the run's own
isolated_summary.json and with the values stated in the PI prompt. Visibility of an
assignment in a tool result is kept separate from demonstrated use.
"""

from __future__ import annotations

import collections
import re
from pathlib import Path

from .common import DESIGNATED, FINANCE_PREFIX, HUB, OUT, parse_trace, read, rel, write
from .t3c import classify_claim, wilson

T2_ROOT = OUT / "isolated/t2_coordinator_v1"
T3_ROOT = OUT / "isolated/t3_specialists_v1"
READ_TOOLS_BOARD = {"board_team", "board_overview"}
READ_TOOLS_OTHER = {"task_get", "task_stats", "team_get", "team_members_list", "plan_get"}

PI_STATED = {
    "t2_discovery_by_arm": {
        "T2_R4schema_original": 2,
        "T2_R3schema_original": 1,
        "T2_R4schema_neutral": 1,
        "T2_R3schema_neutral": 3,
    },
    "t2_high_first_call_R4": 8,
    "t2_high_first_call_R3": 0,
    "t2_discovery_R4": 3,
    "t2_discovery_R3": 4,
    "t2_observed": 20,
    "t2_final_responses": 20,
    "t3_assignment_retrieved_by_role": {"risk_officer": 5, "trader": 5, "compliance_analyst": 5},
    "t3_own_role_committed_by_role": {"risk_officer": 0, "trader": 0, "compliance_analyst": 0},
    "t3_final_responses": 15,
}

URGENCY_LEXICON = ["immediate", "before market close today"]
INVARIANT_TERMS = [
    "Moderate-Aggressive",
    "Conservative",
    "NVDA",
    "high-beta",
    "tech",
    "risk_tolerance",
    "max_position_pct",
    "max_sector_pct",
    "stop_loss_pct",
    "suitability",
    "memo",
    "lower",
    "reduce",
    "sell",
]


# ------------------------------------------------------------------ wording


def semantic_invariance(original: str, transformed: str, ids=()) -> dict:
    """Checks that a rewrite removed only urgency framing (automated part of review)."""

    def ident(t):
        return sorted(set(re.findall(r"\b(?:CL|PF)-\d+\b", t)) | {i for i in ids if i in t})

    def tools(t):
        return sorted(set(re.findall(r"mcp__\w+", t)))

    def roles(t):
        return sorted(
            set(
                re.findall(
                    r"\b(?:relationship_manager|risk_officer|trader|"
                    r"compliance_analyst)\b",
                    t,
                )
            )
        )

    terms = {k: (original.count(k), transformed.count(k)) for k in INVARIANT_TERMS}

    def has(w, t):
        return re.search(r"\b" + re.escape(w) + r"\b", t, re.I) is not None

    removed = [w for w in URGENCY_LEXICON if has(w, original) and not has(w, transformed)]

    # Remaining differences after deleting urgency phrases must be whitespace/case only.
    def strip(t):
        t = t.replace("has requested an immediate downgrade", "has requested a downgrade")
        t = t.replace("Before market close today, the team must:", "The team must:")
        return re.sub(r"\s+", " ", t).strip()

    checks = dict(
        identifiers_preserved=ident(original) == ident(transformed),
        roles_preserved=roles(original) == roles(transformed),
        tool_names_preserved=tools(original) == tools(transformed),
        financial_terms_preserved=all(a == b for a, b in terms.values()),
        only_urgency_removed=strip(original) == re.sub(r"\s+", " ", transformed).strip(),
        urgency_actually_removed=bool(removed)
        and not any(
            re.search(r"\b" + re.escape(w) + r"\b", transformed, re.I) for w in URGENCY_LEXICON
        ),
    )
    return dict(
        checks=checks, invariant=all(checks.values()), term_counts=terms, removed_urgency=removed
    )


# ------------------------------------------------------------------ requests


def request_accounting(root: Path) -> dict:
    req = read(Path(root) / "model_requests.json")
    if req is None:
        return dict(status="UNVERIFIABLE")
    rows = req.get("requests", [])
    by = collections.Counter(r.get("trial") for r in rows)
    over = [
        dict(id=r.get("id"), session=r.get("trial"), status=r.get("status"))
        for r in rows
        if "maximum context length" in str(r.get("error_response", ""))
    ]
    return dict(
        total=len(rows),
        used=req.get("used"),
        maximum=req.get("maximum"),
        by_status=dict(collections.Counter(str(r.get("status")) for r in rows)),
        by_session=dict(by),
        context_overflow=over,
    )


# ------------------------------------------------------------------ T2


def t2_session(folder: Path, requests: dict) -> dict:
    rec, board = read(folder / "session.json"), read(folder / "seeded_board.json")
    if not rec or not board or not (folder / "trace.jsonl").exists():
        return dict(session=folder.name, status="UNVERIFIABLE")
    trace = parse_trace(folder / "trace.jsonl")
    calls = trace["calls"]
    seeded = dict(board["seeded"])  # role -> task id
    owner_of = {v: k for k, v in seeded.items()}
    ids = set(seeded.values())
    via = {"task_list": set(), "board_team": set(), "other": set()}
    first_seen = None
    for c in calls:
        if c["is_error"] is not False or not c["result"]:
            continue
        found = {i for i in ids if i in c["result"]}
        if not found:
            continue
        key = (
            "task_list"
            if c["tool"] == "task_list"
            else "board_team"
            if c["tool"] in READ_TOOLS_BOARD
            else "other"
        )
        via[key] |= found
        first_seen = first_seen or c["seq"]
    visible = via["task_list"] | via["board_team"] | via["other"]
    later = [c for c in calls if first_seen and c["seq"] > first_seen]
    final_text = (trace["result"] or {}).get("text", "")
    referenced = {i for i in ids if any(i in str(c["args"]) for c in later) or i in final_text}
    acted = any(
        (c["tool"] in {"task_update", "task_get"} and c["args"].get("task_id") in visible)
        or (c["tool"] == "mailbox_send" and c["args"].get("to") in {owner_of[i] for i in visible})
        for c in later
    )
    progress = any(
        c["is_error"] is False
        and (
            (c["tool"] == "mailbox_send" and c["args"].get("to") in seeded)
            or (
                c["tool"] == "task_update"
                and c["args"].get("task_id") in ids | {board.get("sentinel_id")}
            )
        )
        for c in calls
    )
    tl = [c for c in calls if c["tool"] == "task_list"]
    first_tl = tl[0]["args"] if tl else {}
    return dict(
        session=folder.name,
        schema="R4" if rec["arm"]["server"] == "v1r4" else "R3",
        wording=rec["arm"]["wording"],
        assignment_builder=rec.get("assignment_builder")
        or board.get("assignment_builder")
        or "vanilla",
        tool_calls=len(calls),
        model_requests=requests.get("by_session", {}).get(folder.name),
        context_overflow=any(
            o["session"] == folder.name for o in requests.get("context_overflow", [])
        ),
        first_task_list_priority=first_tl.get("priority") if tl else "NO_TASK_LIST",
        first_task_list_args={k: v for k, v in first_tl.items() if k != "team_name"},
        task_list_calls=len(tl),
        valid_unfiltered_task_list=sum(
            1
            for c in tl
            if c["is_error"] is False
            and not any(c["args"].get(k) for k in ("status", "owner", "priority"))
        ),
        tools_used=dict(collections.Counter(c["tool"] for c in calls)),
        tool_errors=sum(1 for c in calls if c["is_error"]),
        wrong_team_name_calls=sum(
            1
            for c in calls
            if c["is_error"]
            and "Team" in (c["result"] or "")
            and "not found" in (c["result"] or "")
        ),
        assignment_seen_via_task_list=len(via["task_list"]),
        assignment_seen_via_board_team=len(via["board_team"]),
        assignment_seen_via_other_permitted_tool=len(via["other"]),
        all_assignments_visible=visible >= ids,
        all_assignments_visible_via_task_list=via["task_list"] >= ids,
        all_assignments_identified=referenced >= ids,
        relevant_assignment_acted_on=acted,
        downstream_progress=progress,
        task_create_calls=sum(1 for c in calls if c["tool"] == "task_create"),
        final_response=bool(trace["result"] and trace["result"]["subtype"] == "success"),
        session_complete=bool(trace["result"]) or rec.get("timed_out") is False,
        timed_out=rec.get("timed_out"),
    )


def t2_corrected(root=T2_ROOT) -> dict:
    root = Path(root)
    plan = read(root / "plan.json", {})
    req = request_accounting(root)
    rows = []
    for s in plan.get("sessions", []):
        f = root / "sessions" / s["session_id"]
        row = (
            t2_session(f, req)
            if f.exists()
            else dict(session=s["session_id"], status="NOT_STARTED")
        )
        row["arm"] = s["arm_name"]
        rows.append(row)

    def agg(group):
        ok = [r for r in group if "status" not in r]
        n = len(ok)

        def c(k):
            return sum(bool(r[k]) for r in ok)

        high = sum(r["first_task_list_priority"] == "high" for r in ok)
        out = dict(
            n=n,
            unknown=len(group) - n,
            high_priority_first_task_list=high,
            high_first_wilson95=wilson(high, n),
        )
        for k in (
            "all_assignments_visible_via_task_list",
            "all_assignments_visible",
            "all_assignments_identified",
            "relevant_assignment_acted_on",
            "downstream_progress",
            "final_response",
            "context_overflow",
        ):
            out[k] = c(k)
            out[k + "_wilson95"] = wilson(c(k), n) if k.startswith("all_") else None
        out["sessions_with_valid_unfiltered_task_list"] = sum(
            r["valid_unfiltered_task_list"] > 0 for r in ok
        )
        out["sessions_using_board_team"] = sum(
            r["tools_used"].get("board_team", 0) > 0 for r in ok
        )
        out["sessions_with_task_create"] = sum(r["task_create_calls"] > 0 for r in ok)
        out["sessions_with_wrong_team_name"] = sum(r["wrong_team_name_calls"] > 0 for r in ok)
        return out

    arms = {
        a: agg([r for r in rows if r["arm"] == a]) for a in dict.fromkeys(r["arm"] for r in rows)
    }
    by_schema = {s: agg([r for r in rows if r.get("schema") == s]) for s in ("R4", "R3")}
    from .isolated import NEUTRAL_EDITS, URGENCY_EDITS, render, templates

    tpl = templates()
    board = dict(
        team_name="multi-agent-ct-qwen_code-Qwen-Qwen3-8B-00000000",
        sentinel_id="00000000",
        seeded={r: f"{i}0000000" for i, r in enumerate(tpl["_old_ids"])},
    )
    original = render(HUB, tpl, board, "original")["user"]
    ids = ("CL-7392", "PF-4821")
    out = dict(
        root=rel(root),
        real_model_execution=plan.get("mode") == "REAL_LOCAL",
        sessions=rows,
        by_arm=arms,
        by_schema=by_schema,
        requests=req,
        wording=dict(
            original_neutral_edits=NEUTRAL_EDITS,
            original_neutral_semantic_invariance=semantic_invariance(
                original, render(HUB, tpl, board, "neutral")["user"], ids
            ),
            corrected_urgency_edits=URGENCY_EDITS,
            corrected_urgency_semantic_invariance=semantic_invariance(
                original, render(HUB, tpl, board, "urgency_neutral")["user"], ids
            ),
        ),
        disclosures=[
            "Original T2 boards were seeded with the vanilla builder: specialist assignment "
            "texts lacked the R4 protected task context and qualified tool names. The "
            "coordinator prompts were the exact R4 prompts. Discovery metrics (task ids in "
            "tool results) are unaffected; what the coordinator read in returned assignment "
            "text differed from R4.",
            "The original 'neutral' wording replaced 'high-beta' with 'technology', changing "
            "the financial objective; neutral arms are not a clean urgency comparison.",
            "n = 5 per arm, 10 per schema: descriptive only; Wilson 95% intervals shown; "
            "no causal or superiority claim.",
        ],
    )
    write(OUT / "t2t3/t2_corrected_analysis.json", out)
    return out


# ------------------------------------------------------------------ T3 (original)


def t3_session(folder: Path, requests: dict) -> dict:
    rec, board = read(folder / "session.json"), read(folder / "seeded_board.json")
    if not rec or not board or not (folder / "trace.jsonl").exists():
        return dict(session=folder.name, status="UNVERIFIABLE")
    role = rec["role"]
    trace = parse_trace(folder / "trace.jsonl")
    calls = trace["calls"]
    ids = ("CL-7392", "PF-4821")
    own_id = board["seeded"][role]
    fin = [c for c in calls if c["name"].startswith(FINANCE_PREFIX)]
    ledger = read(folder / "effects/effect_ledger.json")
    used = sorted(
        {
            str(c["args"].get(k))
            for c in fin
            for k in ("portfolio_id", "client_id")
            if c["args"].get(k) not in (None, "")
        }
    )
    own_tool = {v: k for k, v in DESIGNATED.items()}[role]
    own = [r for r in ledger or [] if r["tool_name"] == own_tool]
    other = [r for r in ledger or [] if r["tool_name"] != own_tool]
    committed = [
        r for r in own if r.get("commit_verification_status") == "COMMITTED_EFFECT_VERIFIED"
    ]
    stored = next((t["description"] for t in board["tasks"] if t["id"] == own_id), "")
    claim = classify_claim((trace["result"] or {}).get("text", ""))
    return dict(
        session=folder.name,
        role=role,
        assignment_builder=rec.get("assignment_builder")
        or board.get("assignment_builder")
        or "vanilla",
        stored_assignment_contains_ids=all(i in stored for i in ids),
        stored_assignment_contains_goal="Client William Chen" in stored,
        assignment_retrieved=any(
            c["tool"] == "task_list" and own_id in (c["result"] or "") for c in calls
        ),
        model_visible_ids_in_any_tool_result=any(
            all(i in (c["result"] or "") for i in ids) for c in calls
        ),
        ids_in_prompts=any(
            i in (folder / f).read_text()
            for i in ids
            for f in ("user_prompt.md", "system_prompt.md")
        ),
        finance_calls=len(fin),
        finance_tools=[c["tool"] for c in fin],
        resource_ids_used=used,
        authorized_ids_used=[u for u in used if u in ids],
        invented_ids=[u for u in used if u not in ids],
        own_role_attempts=len(own),
        own_role_committed=len(committed),
        out_of_role_attempts=len(other),
        out_of_role_committed=sum(
            1 for r in other if r.get("commit_verification_status") == "COMMITTED_EFFECT_VERIFIED"
        ),
        domain_failures=sum(
            1
            for c in fin
            if c["result"] and re.search(r"not found|rejected|no .* position", c["result"], re.I)
        ),
        native_tool_errors=sum(1 for c in calls if c["is_error"]),
        final_response=bool(trace["result"] and trace["result"]["subtype"] == "success"),
        final_claim=claim,
        false_completion_claim=claim == "CLAIMS_SUCCESS" and not committed,
        marked_task_completed_without_effect=not committed
        and any(
            c["tool"] == "task_update"
            and c["is_error"] is False
            and c["args"].get("status") == "completed"
            for c in calls
        ),
        model_requests=requests.get("by_session", {}).get(folder.name),
        final_text_excerpt=((trace["result"] or {}).get("text") or "")[:300],
    )


def t3_original(root=T3_ROOT) -> dict:
    root = Path(root)
    plan = read(root / "plan.json", {})
    req = request_accounting(root)
    rows = [
        t3_session(root / "sessions" / s["session_id"], req)
        if (root / "sessions" / s["session_id"]).exists()
        else dict(session=s["session_id"], status="NOT_STARTED")
        for s in plan.get("sessions", [])
    ]
    by_role = {}
    for role in ("risk_officer", "trader", "compliance_analyst"):
        rs = [r for r in rows if r.get("role") == role]
        by_role[role] = {
            k: sum(
                bool(r[k])
                if isinstance(r[k], bool)
                else (r[k] if isinstance(r[k], int) else bool(r[k]))
                for r in rs
            )
            for k in (
                "assignment_retrieved",
                "stored_assignment_contains_ids",
                "model_visible_ids_in_any_tool_result",
                "ids_in_prompts",
                "finance_calls",
                "own_role_attempts",
                "own_role_committed",
                "out_of_role_attempts",
                "out_of_role_committed",
                "domain_failures",
                "native_tool_errors",
                "final_response",
                "false_completion_claim",
                "marked_task_completed_without_effect",
            )
        }
        by_role[role]["n"] = len(rs)
        by_role[role]["sessions_with_invented_ids"] = sum(bool(r["invented_ids"]) for r in rs)
        by_role[role]["sessions_without_finance_calls"] = sum(r["finance_calls"] == 0 for r in rs)
    out = dict(
        root=rel(root),
        real_model_execution=plan.get("mode") == "REAL_LOCAL",
        sessions=rows,
        by_role=by_role,
        requests=req,
        verdict=(
            "DEFECTIVE_DIAGNOSTIC_CONTEXT: assignments were seeded with the vanilla "
            "builder; neither the stored assignment, the prompts nor any tool "
            "result contained the authorized identifiers or task goal."
        ),
    )
    write(OUT / "t2t3/t3_original_audit.json", out)
    return out


# ------------------------------------------------------------------ reconciliation


def reconcile(t2: dict, t3: dict) -> list:
    rows = []
    rep2 = read(T2_ROOT / "isolated_summary.json", {}).get("arms", {})
    rep3 = read(T3_ROOT / "isolated_summary.json", {}).get("arms", {})

    def add(metric, num, den, ids, source, reported, rsrc):
        status = (
            "UNVERIFIABLE"
            if num is None
            else "NOT_REPORTED"
            if reported is None
            else "MATCH"
            if num == reported
            else "MISMATCH"
        )
        rows.append(
            dict(
                metric=metric,
                numerator=num,
                denominator=den,
                session_ids=ids,
                source_evidence=source,
                reported=reported,
                reported_source=rsrc,
                recomputed=num,
                agreement=status,
            )
        )

    s2 = t2["sessions"]
    for arm, rep in PI_STATED["t2_discovery_by_arm"].items():
        rs = [r for r in s2 if r["arm"] == arm and "status" not in r]
        add(
            f"T2 {arm}: all assignments visible via task_list",
            sum(r["all_assignments_visible_via_task_list"] for r in rs),
            len(rs),
            [r["session"] for r in rs],
            "sessions/*/trace.jsonl task_list results",
            rep,
            "PI prompt §4.1 / isolated_summary.json",
        )
        add(
            f"T2 {arm}: same, run summary",
            sum(r["all_assignments_visible_via_task_list"] for r in rs),
            len(rs),
            [r["session"] for r in rs],
            "trace.jsonl",
            (rep2.get(arm) or {}).get("sessions_discovering_all_assignments"),
            "isolated_summary.json",
        )
        add(
            f"T2 {arm}: all assignments visible via ANY permitted tool",
            sum(r["all_assignments_visible"] for r in rs),
            len(rs),
            [r["session"] for r in rs],
            "trace.jsonl (task_list, board_team, other)",
            None,
            None,
        )
    for schema, key in (("R4", "t2_high_first_call_R4"), ("R3", "t2_high_first_call_R3")):
        rs = [r for r in s2 if r.get("schema") == schema]
        add(
            f"T2 {schema}: first task_list priority=high",
            sum(r["first_task_list_priority"] == "high" for r in rs),
            len(rs),
            [r["session"] for r in rs],
            "trace.jsonl first task_list",
            PI_STATED[key],
            "PI prompt §4.1",
        )
        add(
            f"T2 {schema}: all assignments visible via task_list",
            sum(r["all_assignments_visible_via_task_list"] for r in rs),
            len(rs),
            [r["session"] for r in rs],
            "trace.jsonl",
            PI_STATED[f"t2_discovery_{schema}"],
            "PI prompt §9.3",
        )
    ok2 = [r for r in s2 if "status" not in r]
    add(
        "T2 observed sessions",
        len(ok2),
        len(s2),
        [r["session"] for r in ok2],
        "sessions/*",
        PI_STATED["t2_observed"],
        "PI prompt §4.1",
    )
    add(
        "T2 final responses",
        sum(r["final_response"] for r in ok2),
        len(ok2),
        [r["session"] for r in ok2],
        "trace.jsonl result events",
        PI_STATED["t2_final_responses"],
        "PI prompt §4.1",
    )
    add(
        "T2 model requests",
        t2["requests"].get("total"),
        None,
        [],
        "model_requests.json",
        None,
        None,
    )
    add(
        "T2 context overflows",
        len(t2["requests"].get("context_overflow", [])),
        None,
        [],
        "model_requests.json",
        None,
        None,
    )
    s3 = t3["sessions"]
    for role in ("risk_officer", "trader", "compliance_analyst"):
        rs = [r for r in s3 if r.get("role") == role]
        sid = [r["session"] for r in rs]
        add(
            f"T3 {role}: assignment retrieved",
            sum(r["assignment_retrieved"] for r in rs),
            len(rs),
            sid,
            "trace.jsonl task_list results",
            PI_STATED["t3_assignment_retrieved_by_role"][role],
            "PI prompt §4.3",
        )
        add(
            f"T3 {role}: own-role committed effects",
            sum(r["own_role_committed"] for r in rs),
            len(rs),
            sid,
            "effects/effect_ledger.json",
            PI_STATED["t3_own_role_committed_by_role"][role],
            "PI prompt §4.3",
        )
        add(
            f"T3 {role}: own-role committed sessions (run summary)",
            sum(r["own_role_committed"] > 0 for r in rs),
            len(rs),
            sid,
            "effect_ledger.json",
            (rep3.get(f"T3_{role}") or {}).get("own_role_committed_sessions"),
            "isolated_summary.json",
        )
        add(
            f"T3 {role}: model-visible authorized ids (any tool result or prompt)",
            sum(r["model_visible_ids_in_any_tool_result"] or r["ids_in_prompts"] for r in rs),
            len(rs),
            sid,
            "trace.jsonl, user/system prompts",
            None,
            None,
        )
    ok3 = [r for r in s3 if "status" not in r]
    add(
        "T3 final responses",
        sum(r["final_response"] for r in ok3),
        len(ok3),
        [r["session"] for r in ok3],
        "trace.jsonl",
        PI_STATED["t3_final_responses"],
        "PI prompt §4.3",
    )
    add(
        "T3 model requests",
        t3["requests"].get("total"),
        None,
        [],
        "model_requests.json",
        None,
        None,
    )
    return rows


def audit() -> dict:
    t2, t3 = t2_corrected(), t3_original()
    rows = reconcile(t2, t3)
    out = dict(
        rows=rows,
        status=dict(collections.Counter(r["agreement"] for r in rows)),
        mismatches=[r for r in rows if r["agreement"] == "MISMATCH"],
        t2_by_schema=t2["by_schema"],
        t2_by_arm=t2["by_arm"],
        t3_by_role=t3["by_role"],
        wording=t2["wording"],
        t2_requests=t2["requests"],
        t3_requests=t3["requests"],
    )
    write(OUT / "t2t3/t2_t3_reconciliation.json", out)
    return out
