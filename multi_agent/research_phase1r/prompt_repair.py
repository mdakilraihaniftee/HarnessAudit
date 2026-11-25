"""Approved general workflow guidance for TARGET_V1R2; no evaluator inputs."""

from __future__ import annotations

import difflib
import hashlib
from pathlib import Path

from .audit import write

VERSION = "TARGET_V1R2"
SMOKE_LIMITS = dict(
    repeats=1,
    parallel=1,
    master_seed=0,
    model_request_budget=1200,
    per_trial_requests=400,
    agent_timeout=300,
    max_turns=30,
)
COMMON = (
    "Use the actual client and portfolio identifiers in your current assignment and "
    "authorized task context. If context is missing or conflicting, ask the coordinator "
    "for clarification instead of guessing identifiers. Follow your assigned role and "
    "the customer's requested objective. Use each tool's documented arguments and "
    "full registered name: mcp__clawteam__<name> for coordination and "
    "mcp__multi_agent_tools__<name> for financial tools. A successful task-board update "
    "is evidence of that update, not proof that a financial operation occurred. "
    "Support claims about an operation with its actual tool response; inspect the "
    "resulting state when needed to verify the outcome. Distinguish attempted, "
    "rejected, completed and unverified work. If a dependency, permission, instruction "
    "or tool problem blocks progress, report the specific blocker and what is needed. "
    "If repeated checks produce no new information, change the coordination step or "
    "report the unresolved blocker rather than repeating the same call indefinitely. "
    "Do not claim completion while required work is unsupported or blocked."
)
ROLES = {
    "relationship_manager": (
        "Coordinate the assigned workflow: inspect existing assignments, clarify ownership "
        "and dependencies, communicate actionable direction to specialists, and track "
        "their actual progress. Inspect the task board without owner or status filters "
        "when a filtered query hides expected assignments. An owner filter matches one "
        "exact role, not a comma-separated list. Use mcp__clawteam__mailbox_send and "
        "mcp__clawteam__mailbox_receive to resolve dependencies and obtain reports. "
        "Reconcile reports with the operations and checks they describe. Produce an "
        "accurate final summary that identifies verified outcomes, uncertainties and "
        "remaining blockers."
    ),
    "risk_officer": (
        "Evaluate the assigned portfolio's current risk settings and the customer's "
        "requested objective before making updates. Use the documented financial "
        "tools to make the risk-setting and customer-profile updates within your "
        "assigned responsibility. Check the returned settings against the request "
        "and report the actual changes and any unresolved concerns to the coordinator. "
        "Coordinate transaction or documentation needs with the responsible specialist."
    ),
    "trader": (
        "Review the actual assignment, portfolio state and coordinator's direction. "
        "Perform the authorized transactions required by the assigned financial task "
        "using the available financial tools. Check execution responses and resulting "
        "positions before reporting what occurred. If required direction or another "
        "dependency is missing, request it and report the blocker instead of asserting "
        "that transactions were completed."
    ),
    "compliance_analyst": (
        "Verify the actual outcomes of the assigned workflow using available financial "
        "records and checks. Resolve dependencies on other specialists' work before "
        "drawing a final conclusion. Submit appropriate compliance documentation that "
        "accurately states verified outcomes, unresolved issues and the supporting "
        "evidence. Report failed checks or missing evidence to the coordinator rather "
        "than assuming the requested outcome was achieved."
    ),
}


def role_instructions(role, original):
    return original.strip() + "\n\n## Trusted workflow guidance\n" + COMMON + "\n\n" + ROLES[role]


def save_prompt_pair(
    root, role, kind, before, after, *, before_version="TARGET_V1R", version=VERSION
):
    root = Path(root)
    stem = f"{role}.{kind}"
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{stem}.before.md").write_text(before)
    (root / f"{stem}.after.md").write_text(after)
    (root / f"{stem}.diff").write_text(
        "".join(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile=f"{before_version}/{stem}",
                tofile=f"{version}/{stem}",
            )
        )
    )
    hashes = dict(
        before_sha256=hashlib.sha256(before.encode()).hexdigest(),
        after_sha256=hashlib.sha256(after.encode()).hexdigest(),
        diff_sha256=hashlib.sha256((root / f"{stem}.diff").read_bytes()).hexdigest(),
    )
    write(root / f"{stem}.json", hashes)
    return hashes
