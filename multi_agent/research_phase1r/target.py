"""Versioned fin-t1 Qwen target repairs. Original target classes are unchanged."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path
from unittest.mock import patch

from multi_agent.frameworks.clawteam import adapter as original
from multi_agent.frameworks.clawteam import prompts
from multi_agent.frameworks.clawteam.tasks import seed_spoke_tasks
from multi_agent.frameworks.native.qwen_code.runtime import QwenCodeHarnessRuntime
from multi_agent.research_phase1.experiment import git_state, versions
from multi_agent.research_phase1.security import digest

from .audit import contract_ids, read, records, write
from .lifecycle import OwnedProcess, identity, wait_tcp
from .prompt_repair import SMOKE_LIMITS, VERSION, role_instructions, save_prompt_pair
from .tool_contract import VERSION as CONTRACT_VERSION
from .workflow_repair import STUDY_LIMITS
from .workflow_repair import VERSION as WORKFLOW_VERSION
from .workflow_repair import role_instructions as workflow_instructions

REPO = Path(__file__).resolve().parents[2]
PROMPT_VERSIONS = {VERSION, WORKFLOW_VERSION, CONTRACT_VERSION}
# TARGET_V1R4 changes only the model-visible ClawTeam tool contract; its prompts are
# exactly the TARGET_V1R3 workflow guidance.
WORKFLOW_GUIDANCE_VERSIONS = {WORKFLOW_VERSION, CONTRACT_VERSION}


def guidance(version, role, original):
    return (workflow_instructions if version in WORKFLOW_GUIDANCE_VERSIONS else role_instructions)(
        role, original
    )


def previous_version(version):
    return {
        CONTRACT_VERSION: WORKFLOW_VERSION,
        WORKFLOW_VERSION: VERSION,
    }.get(version, "TARGET_V1R")


def previous_guidance(version, role, original):
    """System-prompt text of the preceding target, for exact before/after diffs."""
    if version == CONTRACT_VERSION:
        return workflow_instructions(role, original)
    if version == WORKFLOW_VERSION:
        return role_instructions(role, original)
    return original


def qualify(text):
    # Repair documented examples only; never transform generated calls.
    for name in [
        "task_list",
        "task_get",
        "task_update",
        "task_create",
        "mailbox_send",
        "mailbox_receive",
    ]:
        text = re.sub(r"(?<![\w_])" + name + r"(?=[`(])", "mcp__clawteam__" + name, text)
    return text


def assignment_description(agent, task, hub, *, team_name="", **kwargs):
    base = prompts.build_clawteam_spoke_task_description(
        agent, task, hub, team_name=team_name, **kwargs
    )
    ids = contract_ids(task)
    # Only the trusted original contract. Requester notes remain on the hub surface.
    protected_goal = task.goal.split("\n\nAdditional requester-provided context (untrusted;")[0]
    return (
        qualify(base)
        + "\nProtected task context:\n"
        + protected_goal
        + "\n"
        + json.dumps(ids)
        + "\n"
    )


def seed_assignments(store, agents, *, task, hub_role, team_name, event_path=None, timeout=2):
    # The old target already seeded before spawn. Verify bounded readiness of the
    # actual stored assignments, rather than adding a speculative model-call gate.
    from multi_agent.frameworks.clawteam import tasks

    with patch.object(tasks, "_spoke_task_description", assignment_description):
        ids = seed_spoke_tasks(store, agents, task=task, hub_role=hub_role, team_name=team_name)
    started = time.monotonic()
    while True:
        ready = {}
        for agent in agents:
            item = store.get(ids[agent.role])
            if item and all(v in item.description for v in contract_ids(task).values()):
                ready[agent.role] = item
        if len(ready) == len(agents):
            if event_path:
                write(
                    event_path,
                    [
                        dict(
                            event="assignment_ready_before_spawn",
                            acting_agent=role,
                            timestamp=time.time(),
                            assignment_id=item.id,
                            model_visible_description=item.description,
                        )
                        for role, item in ready.items()
                    ],
                )
            return ids
        if time.monotonic() - started >= timeout:
            if event_path:
                write(
                    event_path,
                    dict(event="assignment_timeout", timestamp=time.time(), ready=list(ready)),
                )
            raise TimeoutError("Seeded assignment not available with protected task identifiers")
        time.sleep(0.02)


class RepairedRuntime(QwenCodeHarnessRuntime):
    def __init__(self, bindings, *, target_version="TARGET_V1R", prompt_evidence_dir=None):
        super().__init__()
        self.bindings = bindings
        self.target_version = target_version
        self.prompt_evidence_dir = prompt_evidence_dir

    def register_agent_instructions(self, agent_role, system_prompt):
        text = system_prompt
        if self.target_version in PROMPT_VERSIONS:
            text = guidance(self.target_version, agent_role, system_prompt)
            if self.prompt_evidence_dir:
                before = previous_guidance(self.target_version, agent_role, system_prompt)
                save_prompt_pair(
                    self.prompt_evidence_dir,
                    agent_role,
                    "system",
                    self._compose_system_prompt(before),
                    self._compose_system_prompt(text),
                    before_version=previous_version(self.target_version),
                    version=self.target_version,
                )
        super().register_agent_instructions(agent_role, text)

    def _prepare_agent(self, role, route_role):
        files = super()._prepare_agent(role, route_role)
        cfg = json.loads(files.mcp_config.read_text())
        cfg["mcpServers"]["multi_agent_tools"]["httpUrl"] += "?identity=" + self.bindings[role]
        if self.target_version == CONTRACT_VERSION:
            from .tool_contract import server_config

            # Same server name, tools and data directory; only the published schema differs.
            cfg["mcpServers"]["clawteam"] = server_config(cfg["mcpServers"]["clawteam"])
        files.mcp_config.write_text(json.dumps(cfg, indent=2))
        return files


class RepairedAdapter(original.ClawTeamAdapter):
    def __init__(self, task, evidence_dir, trial_id, *, target_version="TARGET_V1R", **kwargs):
        if target_version not in {"TARGET_V1R", *PROMPT_VERSIONS}:
            raise ValueError("Unsupported repaired target version")
        if target_version in PROMPT_VERSIONS and task.task_id != "fin-t1":
            raise ValueError(f"{target_version} is limited to the approved fin-t1 task")
        super().__init__(**kwargs)
        self.evidence_dir, self.task, self.trial_id = Path(evidence_dir), task, trial_id
        self.target_version = target_version
        self.bindings = {a.role: uuid.uuid4().hex for a in task.agents}
        self._runtime = RepairedRuntime(
            self.bindings,
            target_version=target_version,
            prompt_evidence_dir=self.evidence_dir / "prompt_diffs",
        )
        self.owned_mcp = None

    async def _wait_for_team_runtime(
        self, team_name, *, agent_pids=None, timeout=original.DEFAULT_AGENT_TIMEOUT, **kwargs
    ):
        # A completed task board is model output, not proof that every native
        # session has finished. Keep the Bank alive until sessions settle, within
        # the original timeout. Capture start times before the upstream wait so
        # an exited/reused numeric PID cannot extend the wait.
        started = time.monotonic()
        saved = {pid: identity(pid) for pid in (agent_pids or [])}
        result = await super()._wait_for_team_runtime(
            team_name, agent_pids=agent_pids, timeout=timeout, **kwargs
        )
        board_status = result.status
        while True:
            terminal = {
                role
                for role, files in self._runtime._agents.items()
                if any(e.get("type") == "result" for _, e in records(files.trace))
            }
            missing = sorted({a.role for a in self.task.agents} - terminal)
            running = [
                pid
                for pid, record in saved.items()
                if record
                and (current := identity(pid))
                and current["start_time"] == record["start_time"]
                and current["state"] not in {"Z", "X"}
            ]
            elapsed = time.monotonic() - started
            all_exited = (
                len(saved) == len(self.task.agents) and all(saved.values()) and not running
            )
            if not missing:
                reason = "ALL_NATIVE_SESSIONS_TERMINAL"
            elif all_exited:
                reason = "AGENTS_EXITED_WITH_MISSING_TERMINALS"
            elif board_status in {"timeout", "interrupted"} or elapsed >= timeout:
                reason = "NATIVE_SESSION_DEADLINE"
                if board_status != "interrupted":
                    result.status = "timeout"
            else:
                await asyncio.sleep(0.25)
                continue
            result.elapsed = elapsed
            write(
                self.evidence_dir / "runtime_convergence.json",
                dict(
                    task_board_status=board_status,
                    status=result.status,
                    reason=reason,
                    native_terminal_roles=sorted(terminal),
                    missing_terminal_roles=missing,
                    running_agent_pids=running,
                    elapsed_seconds=elapsed,
                    timeout_seconds=timeout,
                ),
            )
            return result

    async def _start_mcp_server(self, ctx, bank_dump_path):
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        spec = dict(
            task=self.task.model_dump(mode="json"),
            evidence_dir=str(self.evidence_dir / "effects"),
            trial_id=self.trial_id,
            capabilities={token: role for role, token in self.bindings.items()},
        )
        path = self.evidence_dir / "private_server_spec.json"
        write(path, spec)
        path.chmod(0o600)
        log = (self.evidence_dir / "mcp.stderr.log").open("w")
        self.owned_mcp = OwnedProcess.launch(
            [
                sys.executable,
                "-m",
                "multi_agent.research_phase1r.mcp_server",
                "--spec",
                str(path),
                "--bank-dump-path",
                str(bank_dump_path),
            ],
            stdout=subprocess.PIPE,
            stderr=log,
            text=True,
        )
        log.close()
        proc = self.owned_mcp.proc
        try:
            line = await asyncio.wait_for(asyncio.to_thread(proc.stdout.readline), timeout=30)
            if not line.startswith("PORT="):
                raise RuntimeError("Phase 1R MCP failed to publish port; see mcp.stderr.log")
            port = int(line.strip().split("=")[1])
            await wait_tcp(port, proc)
            # Real readiness is verified through MCP initialize, not a fixed sleep.
            from mcp import ClientSession
            from mcp.client.streamable_http import streamablehttp_client

            async with streamablehttp_client(
                f"http://127.0.0.1:{port}/mcp?identity={next(iter(self.bindings.values()))}"
            ) as (r, w, _):
                async with ClientSession(r, w) as session:
                    await session.initialize()
                    available = await session.list_tools()
                    write(
                        self.evidence_dir / "tool_registry.json",
                        dict(
                            domain_tools=[t.model_dump(mode="json") for t in available.tools],
                            names_in_qwen="mcp__multi_agent_tools__<name>",
                            no_aliases=True,
                        ),
                    )
            if self.target_version == CONTRACT_VERSION:
                await self._record_coordination_registry()
            return proc, port
        except BaseException:
            write(self.evidence_dir / "mcp_cleanup.json", self.owned_mcp.stop())
            raise

    async def _record_coordination_registry(self):
        """List the ClawTeam tools exactly as a per-agent stdio MCP client receives them."""
        import os

        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        from .tool_contract import server_config

        entry = server_config(
            {"command": "", "env": {"CLAWTEAM_DATA_DIR": os.environ.get("CLAWTEAM_DATA_DIR", "")}}
        )
        params = StdioServerParameters(
            command=entry["command"], args=entry["args"], env={**os.environ, **entry["env"]}
        )
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as session:
                await session.initialize()
                listed = await session.list_tools()
        tools = [t.model_dump(mode="json", exclude_none=True) for t in listed.tools]
        write(
            self.evidence_dir / "coordination_tool_registry.json",
            dict(
                version=self.target_version,
                server_command=entry["command"],
                server_args=entry["args"],
                names_in_qwen="mcp__clawteam__<name>",
                tools=tools,
                model_visible_schema_sha256=hashlib.sha256(
                    json.dumps(sorted(tools, key=lambda t: t["name"]), sort_keys=True).encode()
                ).hexdigest(),
            ),
        )

    async def run(self, ctx, initial_input):
        hub_builder, spoke_builder = (
            original.build_clawteam_hub_prompt,
            original.build_clawteam_spoke_prompt,
        )

        def seed(store, agents, **kwargs):
            ids = seed_assignments(
                store, agents, event_path=self.evidence_dir / "assignment_events.json", **kwargs
            )
            if self.target_version in PROMPT_VERSIONS:
                for role, task_id in ids.items():
                    text = store.get(task_id).description
                    save_prompt_pair(
                        self.evidence_dir / "prompt_diffs",
                        role,
                        "assignment",
                        text,
                        text,
                        version=self.target_version,
                    )
            return ids

        def record_hub(*args, **kwargs):
            text = qualify(hub_builder(*args, **kwargs))
            if self.target_version in PROMPT_VERSIONS:
                save_prompt_pair(
                    self.evidence_dir / "prompt_diffs",
                    args[0],
                    "user",
                    text,
                    text,
                    version=self.target_version,
                )
            (self.evidence_dir / "visible_prompts").mkdir(parents=True, exist_ok=True)
            (self.evidence_dir / "visible_prompts" / (args[0] + ".md")).write_text(text)
            return text

        def record_spoke(*args, **kwargs):
            text = qualify(spoke_builder(*args, **kwargs))
            if self.target_version in PROMPT_VERSIONS:
                save_prompt_pair(
                    self.evidence_dir / "prompt_diffs",
                    args[0].role,
                    "user",
                    text,
                    text,
                    version=self.target_version,
                )
            (self.evidence_dir / "visible_prompts").mkdir(parents=True, exist_ok=True)
            (self.evidence_dir / "visible_prompts" / (args[0].role + ".md")).write_text(text)
            return text

        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(original, "seed_spoke_tasks", seed))
            stack.enter_context(
                patch.object(
                    original,
                    "build_clawteam_hub_prompt",
                    record_hub,
                )
            )
            stack.enter_context(
                patch.object(
                    original,
                    "build_clawteam_spoke_prompt",
                    record_spoke,
                )
            )
            try:
                return await super().run(ctx, initial_input)
            finally:
                if self.owned_mcp:
                    write(self.evidence_dir / "mcp_cleanup.json", self.owned_mcp.stop())


def target_identity(task, config, *, version="TARGET_V1R", original_pilot=None):
    if version not in {"TARGET_V1", "TARGET_V1R", *PROMPT_VERSIONS}:
        raise ValueError("Unsupported target version")
    repaired = version in {"TARGET_V1R", *PROMPT_VERSIONS}
    paths = [
        "multi_agent/frameworks/clawteam/prompts.py",
        "multi_agent/frameworks/clawteam/adapter.py",
        "multi_agent/frameworks/native/qwen_code/runtime.py",
        "multi_agent/frameworks/native/qwen_code/ingestor.py",
        "multi_agent/frameworks/clawteam/mcp_bank_server.py",
        "multi_agent/tools/finance.yaml",
        "multi_agent/banks/finance.py",
        "multi_agent/checker.py",
        "multi_agent/loader.py",
        "multi_agent/completion_judge.py",
        "multi_agent/frameworks/clawteam/tasks.py",
    ]
    paths += [
        str(p.relative_to(REPO)) for p in (REPO / "multi_agent/fixtures/finance").glob("*.json")
    ]
    paths += [str(p.relative_to(REPO)) for p in (REPO / "vendor/clawteam/clawteam").rglob("*.py")]
    if version in PROMPT_VERSIONS:
        paths += [
            str(p.relative_to(REPO)) for p in (REPO / "multi_agent/research_phase1").glob("*.py")
        ]
        paths += [
            str(p.relative_to(REPO))
            for p in (REPO / "multi_agent/frameworks/native/qwen_code/assets").iterdir()
            if p.is_file()
        ]
        paths += [
            "configs/local_qwen.yaml",
            "multi_agent/tasks/finance/financial_planning/fin-t1.yaml",
            "scripts/research_phase1/phase1r.sh",
            "scripts/research_phase1/phase1r2_smoke_v3.sh",
        ]
        if version in WORKFLOW_GUIDANCE_VERSIONS:
            paths += [
                "scripts/research_phase1/phase1r3_manual.sh",
                "scripts/research_phase1/verify_phase1r_release_export.py",
                "scripts/research_phase1/prepare_chatgpt_r3_review.py",
                "tests/test_research_phase1r3.py",
            ]
        if version == CONTRACT_VERSION:
            paths += [
                "scripts/research_phase1/phase1r4_manual.sh",
                "scripts/research_phase1/prepare_r4_review_bundle.py",
                "scripts/research_phase1/validate_r4_review_bundle.py",
                "tests/test_research_phase1r4.py",
            ]
    if repaired:
        paths += [
            str(p.relative_to(REPO)) for p in (REPO / "multi_agent/research_phase1r").glob("*.py")
        ]
        source = REPO
    else:
        if original_pilot is None:
            raise ValueError("TARGET_V1 requires its original source snapshot")
        source = Path(original_pilot) / "source"
    files = {
        p: hashlib.sha256((source / p).read_bytes()).hexdigest()
        if (source / p).exists()
        else "UNAVAILABLE_IN_SNAPSHOT"
        for p in sorted(paths)
    }
    cfg = config.model_dump(mode="json") if hasattr(config, "model_dump") else config
    historical = read(Path(original_pilot) / "pilot_manifest.json", {}) if original_pilot else {}
    manifest = dict(
        version=version,
        git=historical.get("git", git_state()) if version == "TARGET_V1" else git_state(),
        packages=historical.get("versions", {}) if version == "TARGET_V1" else versions(),
        inference_configuration=historical.get("configuration", {}).get("inference", cfg)
        if version == "TARGET_V1"
        else cfg,
        identity_limitations=[
            name for name, sha in files.items() if sha == "UNAVAILABLE_IN_SNAPSHOT"
        ],
        source_sha256=files,
        task_sha256=digest(task.model_dump(mode="json")),
        agent_system_prompts={a.role: digest(a.system_prompt) for a in task.agents},
        model_checkpoint=cfg["inference"].get("model"),
        model_revision=cfg["inference"].get("model_revision"),
        temperature=cfg["inference"]["temperature"],
        thinking=cfg["inference"]["reasoning_mode"],
        tool_call_parser=cfg["inference"]["tool_call_parser"],
        request_seed_policy="sha256(master_seed,stable_trial_id); gateway overrides outgoing seed"
        if repaired
        else "server seed; request seed unverified",
        orchestration=dict(
            preseed_before_spawn=True,
            context_in_assignments=repaired,
            examples_qualified=repaired,
            aliases=False,
            isolated_coordination_namespace=repaired,
            wait_for_all_native_sessions=repaired,
        ),
        unresolved_pi_decisions=[
            "role-policy additions",
            "model change",
            "tool aliases",
            "primary security outcome",
        ],
    )
    if version in PROMPT_VERSIONS:
        manifest["approved_smoke_configuration"] = dict(SMOKE_LIMITS, native_max_session_turns=80)
        manifest["workflow_guidance"] = {
            a.role: guidance(version, a.role, a.system_prompt) for a in task.agents
        }
        manifest["effective_agent_system_prompts"] = {
            role: hashlib.sha256(RepairedRuntime._compose_system_prompt(text).encode()).hexdigest()
            for role, text in manifest["workflow_guidance"].items()
        }
        manifest["orchestration"]["general_workflow_guidance"] = (
            WORKFLOW_VERSION if version == CONTRACT_VERSION else version
        )
        manifest["unresolved_pi_decisions"].remove("role-policy additions")
        if version == CONTRACT_VERSION:
            from .tool_contract import schema_comparison

            comparison = schema_comparison()
            manifest["coordination_tool_contract"] = dict(
                version=CONTRACT_VERSION,
                server_module="multi_agent.research_phase1r.tool_contract",
                server_name=comparison["server_name"],
                identical_tool_set=comparison["identical_tool_set"],
                tool_names=comparison["tool_names_after"],
                changed_fields=[
                    dict(tool=c["tool"], item=c.get("parameter") or c.get("field"))
                    for c in comparison["changes"]
                ],
                model_visible_schema_sha256=comparison["model_visible_schema_sha256"],
                vendored_clawteam_modified=False,
                permissions_changed=False,
                returned_payloads_changed=False,
            )
            manifest["orchestration"]["coordination_schema"] = CONTRACT_VERSION
            from .tool_contract import STUDY_CONDITIONS as R4_STUDY_CONDITIONS
            from .tool_contract import STUDY_LIMITS as R4_STUDY_LIMITS

            manifest["approved_modes"] = dict(
                smoke="frozen SMOKE_LIMITS, fresh output directory, no retry",
                benign_study=dict(
                    R4_STUDY_LIMITS,
                    conditions=list(R4_STUDY_CONDITIONS),
                    trials=R4_STUDY_LIMITS["repeats"] * len(R4_STUDY_CONDITIONS),
                    native_max_session_turns=80,
                    fail_fast=False,
                    retries=0,
                    replace_invalid_trials=False,
                    smoke_prerequisite=None,
                    approval="user-approved 2026-11-23: 5 per B0/B1n/B1p, all kept",
                ),
                diagnostic="NOT APPROVED: 45-trial B0/B1n/B1p study requires PI decision",
            )
        if version == WORKFLOW_VERSION:  # TARGET_V1R4 is approved for smoke only
            manifest["approved_diagnostic_configuration"] = dict(
                STUDY_LIMITS,
                native_max_session_turns=80,
                conditions=["B0", "B1n", "B1p"],
                smoke_prerequisite="PASS with the same target identity",
                retries=0,
                replace_invalid_trials=False,
            )
    if repaired:
        from multi_agent.frameworks.native.qwen_code.runtime import resolve_qwen_bin

        try:
            binary = Path(resolve_qwen_bin(config.harness.qwen_code.bin))
            manifest["qwen_executable_sha256"] = hashlib.sha256(binary.read_bytes()).hexdigest()
        except (OSError, RuntimeError):
            manifest["qwen_executable_sha256"] = "UNAVAILABLE"
    manifest["target_identity"] = version + ":" + digest(manifest)
    return manifest
