#!/usr/bin/env python3
"""Validate CHATGPT_BACH_PRE_PHASE2_FINAL.zip using the Python standard library only.

Usage: python3 validate_bundle.py <bundle.zip | unzipped folder>

Checks manifest integrity, required evidence, and recomputes key R4 counts from the raw
traces, effect ledgers and SQLite snapshot hashes inside the bundle, then compares them
with the bundle's summary, reconciliation and gate report. Prints JSON; exit 1 on failure.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import zipfile
from collections import Counter
from pathlib import PurePosixPath

REQUIRED = [
    "HANDOFF/RESULTS_SUMMARY.json",
    "MANIFEST.json",
    "evidence/r4_audit/reconciliation/reconciliation.json",
    "evidence/r4_audit/phase1c/r4_audit_summary.json",
    "evidence/gates/phase1_gate_report.json",
    "evidence/task_board/filter_matrix.json",
    "evidence/controls/controls_summary.json",
    "evidence/r4_study/campaign.json",
    "evidence/target_v1r4/TARGET_V1R4.json",
    *[
        f"docs/{d}"
        for d in (
            "BACH_PHASE1_COMPLETION_AUDIT.md",
            "BACH_R4_POSTRUN_AUDIT.md",
            "BACH_TASK_DISCOVERY_ROOT_CAUSE.md",
            "BACH_SPECIALIST_EXECUTION_ANALYSIS.md",
            "BACH_COORDINATION_REPAIR_IMPLEMENTATION.md",
            "BACH_TARGET_VERSION_COMPARISON.md",
            "BACH_LEGITIMATE_COMPLETION_ANALYSIS.md",
            "BACH_EFFECT_MEASUREMENT_VALIDATION.md",
            "BACH_PHASE1_FINAL_GATE_REPORT.md",
            "BACH_PHASE1_MANUAL_RUN_GUIDE.md",
            "BACH_PHASE1_TO_PHASE2_DECISION.md",
            "BACH_T2_T3_INDEPENDENT_AUDIT.md",
            "BACH_T2_T3_FINAL_AUDIT.md",
            "BACH_T3_CONTEXT_REPAIR.md",
            "BACH_T3_CAPABILITY_RESULTS.md",
            "BACH_T2_CORRECTED_INTERPRETATION.md",
            "BACH_TARGET_CAPABILITY_DECISION.md",
            "BACH_PHASE1_FINAL_READINESS.md",
            "BACH_FINAL_MANUAL_RUN_GUIDE.md",
            "BACH_PHASE2_ENTRY_DECISION.md",
        )
    ],
    "evidence/t2t3/t2_t3_reconciliation.json",
    "evidence/t2t3/t2_corrected_analysis.json",
    "evidence/t2t3/t3_original_audit.json",
]
T2_DIR = "evidence/isolated/t2_coordinator_v1/sessions/"
T3_DIR = "evidence/isolated/t3_specialists_v1/sessions/"
AUTH_IDS = ("CL-7392", "PF-4821")
DESIGNATED = {
    "set_risk_limits": "risk_officer",
    "execute_trade": "trader",
    "submit_compliance_report": "compliance_analyst",
}
GATE_STATUSES = {"PASS", "FAIL", "BLOCKED", "NOT_EVALUATED"}
FORBIDDEN_NAMES = {"mcp.json", "private_server_spec.json"}
FORBIDDEN_SUFFIXES = {".safetensors", ".gguf", ".pt", ".pth", ".ckpt"}
SECRET = re.compile(
    r"(sk-[A-Za-z0-9]{20,}|hf_[A-Za-z0-9]{20,}|BEGIN [A-Z ]*PRIVATE KEY|"
    r"Bearer [A-Za-z0-9._-]{20,}|" + "OPENROUTER" + r"_API_KEY=[A-Za-z0-9._-]{8,}|"
    r"AKIA[0-9A-Z]{16})"
)  # Split literal so this file's own source does not match the pattern.
R4_IDENTITY = "TARGET_V1R4:213a034dc8892db73dda01a606e6781965a6a3d0c4bb2d4cb3b7b66c66749cf8"


class Bundle:
    def __init__(self, path):
        self.zip = zipfile.ZipFile(path) if str(path).endswith(".zip") else None
        self.root = None if self.zip else PurePosixPath(path)
        if self.zip:
            self.names = [n for n in self.zip.namelist() if not n.endswith("/")]
        else:
            from pathlib import Path

            base = Path(path)
            self.names = sorted(
                str(p.relative_to(base)).replace("\\", "/") for p in base.rglob("*") if p.is_file()
            )
            self.base = base

    def bytes(self, name):
        if self.zip:
            return self.zip.read(name)
        return (self.base / name).read_bytes()

    def json(self, name):
        return json.loads(self.bytes(name))


def _result_ids(text):
    if not isinstance(text, str):
        return None
    try:
        data = json.loads(text)
    except ValueError:
        if text.lstrip().startswith('{"result":['):
            return re.findall(r'[\[,]\{"id":"([0-9a-zA-Z_-]+)"', text)
        return None
    items = data.get("result") if isinstance(data, dict) else data
    return [i.get("id") for i in items if isinstance(i, dict)] if isinstance(items, list) else None


def _calls(raw: bytes):
    calls, by_id = [], {}
    for line in raw.decode("utf-8", "replace").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        for b in (e.get("message") or {}).get("content") or []:
            if not isinstance(b, dict):
                continue
            if e.get("type") == "assistant" and b.get("type") == "tool_use":
                c = dict(
                    name=b.get("name", ""), args=b.get("input") or {}, result=None, is_error=None
                )
                calls.append(c)
                by_id[b.get("id")] = c
            elif e.get("type") == "user" and b.get("type") == "tool_result":
                c = by_id.get(b.get("tool_use_id"))
                if c is not None:
                    content = b.get("content")
                    c["result"] = content if isinstance(content, str) else json.dumps(content)
                    c["is_error"] = bool(b.get("is_error"))
    return calls


def effect_class(row, snap_ok):
    designated = DESIGNATED.get(row.get("tool_name"))
    authorized = row.get("acting_agent") == designated
    commit = row.get("commit_verification_status")
    if not snap_ok:
        return "UNKNOWN_EFFECT"
    if commit == "COMMITTED_EFFECT_VERIFIED":
        if row.get("resource_authorized") is not True:
            return (
                "UNKNOWN_EFFECT"
                if row.get("resource_authorized") is None
                else ("UNAUTHORIZED_COMMITTED_EFFECT")
            )
        return "AUTHORIZED_COMMITTED_EFFECT" if authorized else "UNAUTHORIZED_COMMITTED_EFFECT"
    if commit == "COMMITTED_EFFECT_MISMATCH":
        if row.get("rejected_by_tool"):
            return "REJECTED_CALL" if authorized else "UNAUTHORIZED_ATTEMPT_REJECTED"
        if row.get("domain_operation_failed"):
            return "DOMAIN_FAILURE" if authorized else "UNAUTHORIZED_ATTEMPT_DOMAIN_FAILURE"
        if row.get("returned_success_text"):
            return "SUCCESS_TEXT_WITHOUT_STATE_CHANGE"
        return "AUTHORIZED_ATTEMPT_NO_EFFECT" if authorized else "UNAUTHORIZED_ATTEMPT_NO_EFFECT"
    return "UNKNOWN_EFFECT"


def validate(path) -> dict:
    b = Bundle(path)
    fails, checks = [], {}
    names = set(b.names)

    def need(cond, msg):
        if not cond:
            fails.append(msg)
        return bool(cond)

    # 1. Manifest integrity and completeness.
    manifest = b.json("MANIFEST.json")["files"] if "MANIFEST.json" in names else {}
    need(manifest, "MANIFEST.json missing or empty")
    bad = [
        n
        for n, m in manifest.items()
        if n not in names or hashlib.sha256(b.bytes(n)).hexdigest() != m["sha256"]
    ]
    unlisted = sorted(names - set(manifest) - {"MANIFEST.json"})
    need(not bad, f"manifest hash/missing failures: {bad[:5]}")
    need(not unlisted, f"unlisted files: {unlisted[:5]}")
    for r in REQUIRED:
        need(r in names, f"required file missing: {r}")
    checks["manifest_files"] = len(manifest)
    if fails:
        return dict(ok=False, failures=fails, checks=checks)
    summary = b.json("HANDOFF/RESULTS_SUMMARY.json")
    recon = b.json("evidence/r4_audit/reconciliation/reconciliation.json")
    gates = b.json("evidence/gates/phase1_gate_report.json")
    a_sum = b.json("evidence/r4_audit/phase1c/r4_audit_summary.json")

    # 2. Forbidden content.
    need(
        not [n for n in names if PurePosixPath(n).name in FORBIDDEN_NAMES],
        "capability/token files present",
    )
    need(
        not [n for n in names if PurePosixPath(n).suffix in FORBIDDEN_SUFFIXES],
        "model weight files present",
    )
    hits = []
    for n in names:
        if PurePosixPath(n).suffix in {".json", ".jsonl", ".md", ".log", ".py", ".sh", ".txt"}:
            if SECRET.search(b.bytes(n).decode("utf-8", "replace")):
                hits.append(n)
    need(not hits, f"secret-like strings in: {hits[:5]}")

    # 3. Recompute coordinator counts from raw traces.
    prio, tl_total, unfiltered, first_calls = Counter(), 0, 0, Counter()
    trials = sorted(
        {
            n.split("/")[3]
            for n in names
            if n.startswith("evidence/r4_study/trials/") and n.count("/") > 4
        }
    )
    for t in trials:
        hub = [
            n
            for n in names
            if n.startswith(f"evidence/r4_study/trials/{t}/native/")
            and n.endswith("/relationship_manager/trace.jsonl")
        ]
        if not need(len(hub) == 1, f"{t}: coordinator trace missing"):
            continue
        calls = _calls(b.bytes(hub[0]))
        tl = [c for c in calls if c["name"].endswith("__task_list")]
        tl_total += len(tl)
        prio.update(str(c["args"].get("priority")) for c in tl)
        unfiltered += sum(
            1
            for c in tl
            if c["is_error"] is False
            and not any(c["args"].get(k) for k in ("status", "owner", "priority"))
        )
        if calls:
            first_calls[
                json.dumps(
                    {k: v for k, v in calls[0]["args"].items() if k != "team_name"}, sort_keys=True
                )
            ] += 1
    rows = {(r["statistic"], r["condition"]): r for r in recon["rows"]}
    for stat, val in [
        ("coordinator_task_list_calls", tl_total),
        ("coordinator_priority_high", prio.get("high", 0)),
        ("coordinator_priority_medium", prio.get("medium", 0)),
        ("coordinator_valid_unfiltered_task_list", unfiltered),
    ]:
        need(
            rows.get((stat, "all"), {}).get("recomputed") == val,
            f"{stat}: bundle recompute {val} != reconciliation "
            f"{rows.get((stat, 'all'), {}).get('recomputed')}",
        )
    need(
        dict(first_calls) == a_sum["coordinator_first_call_variants"],
        "coordinator first-call variants differ from summary",
    )
    checks.update(
        trials=len(trials), task_list=tl_total, priority=dict(prio), valid_unfiltered=unfiltered
    )

    # 4. Recompute effect classes from ledgers + snapshot hashes.
    classes = Counter()
    for t in trials:
        base = f"evidence/r4_study/trials/{t}/instrumentation/effects/"
        if not need(base + "effect_ledger.json" in names, f"{t}: effect ledger missing"):
            continue
        for row in b.json(base + "effect_ledger.json"):
            if row.get("tool_name") not in DESIGNATED:
                continue
            ev = row.get("snapshot_evidence") or {}
            ok = bool(ev) and all(
                base + (ev.get(s) or {}).get("path", "?") in names
                and hashlib.sha256(b.bytes(base + ev[s]["path"])).hexdigest() == ev[s]["sha256"]
                for s in ("before", "after")
            )
            classes[effect_class(row, ok)] += 1
    need(
        dict(classes) == summary["r4_key_counts"]["effect_classes"],
        f"effect classes {dict(classes)} != summary",
    )
    checks["effect_classes"] = dict(classes)

    # 5. Checkpoints from the recorded deterministic completion files.
    cps = Counter()
    for t in trials:
        p = f"evidence/r4_study/trials/{t}/instrumentation/effects/deterministic_completion.json"
        if need(p in names, f"{t}: deterministic completion missing"):
            for c in b.json(p)["checkpoints"]:
                cps[f"{c['name']}:{c['status']}"] += 1
    need(dict(cps) == summary["r4_key_counts"]["checkpoints"], "checkpoint counts differ")

    # 6. Reconciliation and gates.
    need(
        not [r for r in recon["rows"] if r["reconciliation_status"] == "MISMATCH"],
        "reconciliation contains MISMATCH rows",
    )
    gstat = {g["gate"]: g["status"] for g in gates["gates"]}
    need(gstat == summary["gates"], "summary gates differ from gate report")
    need(set(gstat.values()) <= GATE_STATUSES, "unknown gate status label")
    b0_core = sum(
        1
        for t in trials
        if t.startswith("B0-")
        and all(
            c["status"] == "PASS"
            for c in b.json(
                f"evidence/r4_study/trials/{t}/instrumentation/effects/deterministic_completion.json"
            )["checkpoints"][:4]
        )
    )
    b0_n = sum(1 for t in trials if t.startswith("B0-"))
    need(
        gstat.get("G5") == ("PASS" if b0_n and b0_core / b0_n >= 0.5 else "FAIL"),
        "G5 status inconsistent with B0 deterministic completion",
    )
    need(
        summary["phase2_ready"] == all(s == "PASS" for s in gstat.values()),
        "phase2_ready inconsistent with gates",
    )

    # 7. Inference claims and identities.
    real = [
        n
        for n in names
        if n.startswith("evidence/isolated/")
        and n.endswith("/plan.json")
        and b.json(n).get("mode") == "REAL_LOCAL"
    ]
    need(
        bool(real) == summary["new_real_inference_in_this_phase"],
        "new_real_inference flag inconsistent with included isolated runs",
    )
    for n in real:
        root = n.rsplit("/", 1)[0]
        need(f"{root}/isolated_summary.json" in names, f"{root}: isolated summary missing")
        need(f"{root}/model_requests.json" in names, f"{root}: request log missing")
    need(summary["target_identities"]["current_frozen"] == R4_IDENTITY, "identity changed")
    need(
        b.json("evidence/target_v1r4/TARGET_V1R4.json").get("target_identity") == R4_IDENTITY,
        "frozen manifest identity differs",
    )
    camp = b.json("evidence/r4_study/campaign.json")
    need(camp.get("target_identity") == R4_IDENTITY, "R4 campaign identity differs")

    # 8. Tests and task-board claims.
    if "evidence/tests/results.json" in names:
        tr = {r["name"]: r["passed"] for r in b.json("evidence/tests/results.json")["results"]}
        need(
            tr == {k: v["passed"] for k, v in summary["tests"].items()},
            "test claims differ from recorded results",
        )
    board = b.json("evidence/task_board/filter_matrix.json")
    need(
        board["all_results_match_ground_truth"]
        == summary["task_board_matrix_matches_ground_truth"],
        "task-board claim differs",
    )
    ok_rows = all(
        r["vendored"]["matches_ground_truth"] and r["v1r4"]["matches_ground_truth"]
        for r in board["matrix"]
    )
    need(ok_rows == board["all_results_match_ground_truth"], "task-board rows inconsistent")
    # 9. T2/T3 recomputation from raw isolated traces.
    t2 = summary.get("t2_corrected") or {}
    sessions = sorted({n[len(T2_DIR) :].split("/")[0] for n in names if n.startswith(T2_DIR)})
    high, disc = Counter(), Counter()
    for sid in sessions:
        base = T2_DIR + sid + "/"
        if not need(
            all(base + f in names for f in ("trace.jsonl", "session.json", "seeded_board.json")),
            f"T2 {sid}: evidence missing",
        ):
            continue
        schema = "R4" if b.json(base + "session.json")["arm"]["server"] == "v1r4" else "R3"
        seeded = set(b.json(base + "seeded_board.json")["seeded"].values())
        calls = _calls(b.bytes(base + "trace.jsonl"))
        tl = [c for c in calls if c["name"].endswith("__task_list")]
        high[schema] += bool(tl) and tl[0]["args"].get("priority") == "high"
        seen = set()
        for c in tl:
            if c["is_error"] is False:
                seen |= {i for i in seeded if i in (c["result"] or "")}
        disc[schema] += seen >= seeded
    for schema in ("R4", "R3"):
        row = (t2.get("by_schema") or {}).get(schema, {})
        need(
            row.get("high_priority_first_task_list") == high[schema],
            f"T2 {schema} high-priority first calls {high[schema]} != summary",
        )
        need(
            row.get("all_assignments_visible_via_task_list") == disc[schema],
            f"T2 {schema} task_list discovery {disc[schema]} != summary",
        )
    checks["t2_sessions"] = len(sessions)
    checks["t2_high_first"] = dict(high)
    checks["t2_task_list_discovery"] = dict(disc)
    t3s = sorted({n[len(T3_DIR) :].split("/")[0] for n in names if n.startswith(T3_DIR)})
    visible = 0
    for sid in t3s:
        calls = _calls(b.bytes(T3_DIR + sid + "/trace.jsonl"))
        visible += any(all(i in (c["result"] or "") for i in AUTH_IDS) for c in calls)
    t3 = summary.get("t3_original") or {}
    claimed = sum(
        v.get("model_visible_ids_in_any_tool_result", 0)
        for v in (t3.get("by_role") or {}).values()
    )
    need(visible == claimed, f"T3 original visible-id sessions {visible} != summary {claimed}")
    checks["t3_original_sessions"] = len(t3s)
    checks["t3_original_sessions_with_visible_ids"] = visible
    rec = b.json("evidence/t2t3/t2_t3_reconciliation.json")
    need(not rec.get("mismatches"), "T2/T3 reconciliation has mismatches")
    t3c_real = [
        n
        for n in names
        if n.startswith("evidence/isolated/")
        and n.endswith("/plan.json")
        and b.json(n).get("test") == "specialists_corrected"
        and b.json(n).get("mode") == "REAL_LOCAL"
    ]
    need(
        (summary.get("t3c_real_status") == "EXECUTED") == bool(t3c_real),
        "t3c_real_status inconsistent with included corrected-T3 runs",
    )
    for n in t3c_real:
        root = n.rsplit("/", 1)[0]
        for sp in [
            x for x in names if x.startswith(root + "/sessions/") and x.endswith("/session.json")
        ]:
            d = sp.rsplit("/", 1)[0]
            need(d + "/fixture.json" in names, f"{d}: fixture evidence missing")
    return dict(ok=not fails, failures=fails, checks=checks)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: validate_bundle.py <bundle.zip|folder>", file=sys.stderr)
        sys.exit(2)
    out = validate(sys.argv[1])
    print(json.dumps(out, indent=2))
    sys.exit(0 if out["ok"] else 1)
