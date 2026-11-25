#!/usr/bin/env python3
"""Independent, stdlib-only validation of an extracted BACH R4 reviewer bundle.

Usage: python validate_bundle.py <extracted-bundle-dir>   (or a .zip path)

Fails if any file is missing, modified, unlisted, if key numbers cannot be
recomputed from raw traces, if summary claims disagree with evidence, if an
unrun experiment is reported as a result, or if a credential-like value appears.
It imports nothing from the repository.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import tempfile
import zipfile
from pathlib import Path

REQUIRED = [
    "HANDOFF/README.md",
    "HANDOFF/RESULTS_SUMMARY.json",
    "HANDOFF/NEXT_ACTION_REQUEST.txt",
    "HANDOFF/REPRODUCE.md",
    "docs/research/BACH_R3_INDEPENDENT_ROOT_CAUSE.md",
    "docs/research/BACH_TARGET_V1R4_IMPLEMENTATION.md",
    "docs/research/BACH_V1_TO_V1R4_COMPARISON.md",
    "docs/research/BACH_V1R4_VALIDATION.md",
    "docs/research/BACH_V1R4_MANUAL_RUN_GUIDE.md",
    "docs/research/BACH_NEXT_DECISION.md",
    "source_diffs/r3_to_r4.diff",
    "evidence/target_v1r4/TARGET_V1R4.json",
    "evidence/target_v1r4/release_status.json",
    "evidence/target_v1r4/schema_evidence/tool_schema_comparison.json",
    "evidence/target_v1r4/schema_evidence/qwen_model_visible_clawteam_tools.json",
    "evidence/target_v1r4/schema_evidence/qwen3_chat_template_rendering.json",
    "evidence/target_v1r4/v4_audit/coordinator_call_ledger.json",
    "evidence/target_v1r4/v4_audit/coordination_progress.json",
    "evidence/target_v1r4/v4_audit/specialist_timelines.json",
    "evidence/target_v1r4/v4_audit/effect_attribution.json",
    "evidence/target_v1r4/cross_version_coordinator_ledgers.json",
    "evidence/target_v1r4/tests/results.json",
    "evidence/target_v1r3/TARGET_V1R3.json",
    "evidence/diagnostic_smoke/v4/smoke_acceptance.json",
    "evidence/diagnostic_smoke/v4/trials/B0-0/trial.json",
    "evidence/diagnostic_smoke/v4/trials/B0-0/instrumentation/effects/effect_ledger.json",
    "evidence/diagnostic_smoke/v4/trials/B0-0/instrumentation/effects/deterministic_completion.json",
    "evidence/diagnostic_smoke/v4/trials/B0-0/research_security.json",
]
SECRET = re.compile(
    r"(sk-[A-Za-z0-9]{20,}|hf_[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|"
    r"Bearer (?!EMPTY\b)[A-Za-z0-9._-]{12,}|-----BEGIN [A-Z ]*PRIVATE KEY)"
)


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def load(root, rel):
    return json.loads((root / rel).read_text())


def trace_counts(path):
    calls, errors, empty = 0, 0, 0
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        for block in (event.get("message") or {}).get("content", []) or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                calls += 1
            elif block.get("type") == "tool_result":
                if block.get("is_error"):
                    errors += 1
                else:
                    text = block.get("content")
                    text = text if isinstance(text, str) else json.dumps(text)
                    if text.split("\n", 1)[0].strip() == '{"result":[]}':
                        empty += 1
    return calls, errors, empty


def validate(root):
    root = Path(root)
    errors = []
    manifest = load(root, "MANIFEST.json")
    listed = manifest["files"]
    actual = {
        str(p.relative_to(root))
        for p in root.rglob("*")
        if p.is_file() and p.name != "MANIFEST.json" and "validation_receipt" not in p.name
    }
    for rel, sha in listed.items():
        path = root / rel
        if not path.is_file():
            errors.append(f"missing listed file: {rel}")
        elif hashlib.sha256(path.read_bytes()).hexdigest() != sha:
            errors.append(f"modified file: {rel}")
    for rel in sorted(actual - set(listed)):
        errors.append(f"unlisted file: {rel}")
    for rel in REQUIRED:
        if rel not in listed:
            errors.append(f"required evidence absent: {rel}")
    if errors:
        return dict(passed=False, errors=errors)

    summary = load(root, "HANDOFF/RESULTS_SUMMARY.json")
    # Frozen identities must recompute from their own bodies.
    for rel, version in [
        ("evidence/target_v1r4/TARGET_V1R4.json", "TARGET_V1R4"),
        ("evidence/target_v1r3/TARGET_V1R3.json", "TARGET_V1R3"),
    ]:
        data = load(root, rel)
        body = {k: v for k, v in data.items() if k != "target_identity"}
        if data.get("target_identity") != version + ":" + digest(body):
            errors.append(f"identity does not recompute: {rel}")
    r4 = load(root, "evidence/target_v1r4/TARGET_V1R4.json")
    if summary["target_v1r4"]["identity"] != r4["target_identity"]:
        errors.append("summary V1R4 identity differs from frozen manifest")
    contract = r4.get("coordination_tool_contract", {})
    comparison = load(root, "evidence/target_v1r4/schema_evidence/tool_schema_comparison.json")
    if contract.get("model_visible_schema_sha256") != comparison["model_visible_schema_sha256"]:
        errors.append("schema hash in manifest differs from schema evidence")
    if not comparison["identical_tool_set"]:
        errors.append("tool set changed")

    # Recompute the coordinator failure from the raw v4 trace.
    trace = (
        root / "evidence/diagnostic_smoke/v4/trials/B0-0/native/"
        "ct-qwen_code-Qwen-Qwen3-8B-27d14a0a/relationship_manager/trace.jsonl"
    )
    if not trace.is_file():
        errors.append("raw v4 coordinator trace absent")
    else:
        calls, rejected, empty = trace_counts(trace)
        ledger = load(root, "evidence/target_v1r4/v4_audit/coordinator_call_ledger.json")[
            "summary"
        ]
        expected = summary["v4_coordinator"]
        for name, value in [("calls", calls), ("rejected", rejected), ("empty", empty)]:
            if expected[name] != value:
                errors.append(f"summary v4 {name}={expected[name]} but raw trace gives {value}")
        if (
            ledger["tool_call_count"],
            ledger["rejected_tool_invocations"],
            ledger["empty_responses"],
        ) != (calls, rejected, empty):
            errors.append("derived coordinator ledger disagrees with raw trace")

    # Effect attribution must be internally consistent and match the raw effect ledger.
    attribution = load(root, "evidence/target_v1r4/v4_audit/effect_attribution.json")
    raw = load(
        root, "evidence/diagnostic_smoke/v4/trials/B0-0/instrumentation/effects/effect_ledger.json"
    )
    verified = sum(e.get("commit_verification_status") == "COMMITTED_EFFECT_VERIFIED" for e in raw)
    if attribution["verified_committed_effects"] != verified:
        errors.append("verified effects differ from raw effect ledger")
    outcomes = [r["research_outcome"] for r in attribution["calls"]]
    if attribution["attempted_role_violations"] != (
        attribution["verified_unauthorized_effects"] + attribution["failed_unauthorized_attempts"]
    ):
        errors.append("attempt count double-counts or omits effects")
    for key, label in [
        ("verified_unauthorized_effects", "VERIFIED_UNAUTHORIZED_EFFECT"),
        ("failed_unauthorized_attempts", "FAILED_UNAUTHORIZED_ATTEMPT"),
        ("valid_authorized_effects", "VALID_AUTHORIZED_EFFECT"),
        ("effects_with_unknown_attribution", "UNKNOWN_ATTRIBUTION"),
    ]:
        if attribution[key] != outcomes.count(label):
            errors.append(f"attribution {key} inconsistent with rows")
    security = load(root, "evidence/diagnostic_smoke/v4/trials/B0-0/research_security.json")
    if security["summary"].get("unauthorized_effect_count") is not None:
        errors.append("original research_security summary appears modified")

    # Unrun work must be labelled as unrun; a run must carry its acceptance record.
    v5 = root / "evidence/diagnostic_smoke/v5"
    v5_status = summary["v5_smoke"]["status"]
    if (v5 / "campaign.json").is_file():
        if v5_status == "NOT_RUN":
            errors.append("v5 evidence present but summary says NOT_RUN")
        acceptance = v5 / "smoke_acceptance.json"
        if acceptance.is_file():
            if summary["v5_smoke"].get("passed") != json.loads(acceptance.read_text()).get(
                "passed"
            ):
                errors.append("summary v5 pass/fail differs from smoke_acceptance.json")
        elif summary["v5_smoke"].get("passed") is not None:
            errors.append("v5 pass/fail claimed without smoke_acceptance.json")
    else:
        if v5_status != "NOT_RUN" or summary["v5_smoke"].get("passed") is not None:
            errors.append("v5 reported without v5 evidence")
    study = root / "evidence/diagnostic_calibration/r4_5_per_condition_v1"
    study_summary = summary.get("r4_benign_study", {})
    if (study / "campaign.json").is_file():
        campaign = json.loads((study / "campaign.json").read_text())
        started = sum(a.get("status") != "RESERVED" for a in campaign.get("attempts", []))
        if study_summary.get("status") != "RUN" or study_summary.get("started") != started:
            errors.append("benign study summary disagrees with its campaign.json")
        if study_summary.get("target_identity") != r4["target_identity"]:
            errors.append("benign study ran under a different target identity")
    elif study_summary.get("status") != "NOT_RUN" or study_summary.get("started") is not None:
        errors.append("benign study reported without its evidence")
    for item in summary.get("not_executed", []):
        if not item.get("label", "").startswith("NOT EXECUTED"):
            errors.append(f"unrun item lacks NOT EXECUTED label: {item}")

    tests = load(root, "evidence/target_v1r4/tests/results.json")
    for row in tests.get("tests", []):
        if row.get("status") not in {"PASS", "FAIL", "BLOCKED", "SKIPPED"}:
            errors.append(f"unknown test status: {row}")
    if summary.get("cpu_tests_all_passed") != all(
        r.get("status") == "PASS" for r in tests.get("tests", [])
    ):
        errors.append("summary test claim differs from recorded test results")

    for rel in listed:
        path = root / rel
        if path.suffix in {".json", ".jsonl", ".md", ".txt", ".diff", ".log", ".py", ".sh"}:
            if SECRET.search(path.read_text(errors="replace")):
                errors.append(f"credential-like value in {rel}")
    return dict(passed=not errors, errors=errors, files_checked=len(listed))


def main(argv):
    if len(argv) != 2:
        print(__doc__)
        return 2
    target = Path(argv[1])
    if target.suffix == ".zip":
        with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(target) as archive:
            archive.extractall(tmp)
            result = validate(Path(tmp))
    else:
        result = validate(target)
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
