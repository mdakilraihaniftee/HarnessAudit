"""Qwen Code CLI runtime for ClawTeam execution against a local model server.

Every agent gets an isolated ``QWEN_HOME`` (no user settings, memories, OAuth or
extensions leak in), runs in ``--bare`` mode with an explicit ``--mcp-config``
(the HarnessAudit domain MCP server plus ClawTeam's stdio MCP server), and talks
to the OpenAI-compatible endpoint resolved by the local-inference router.

Native observability: the stream-json output of each agent is captured by a
wrapper (``assets/qwen_wrapper.sh``) into ``agents/<role>/trace.jsonl`` with a
receive timestamp per event, and (by default) every outbound TCP connection and
DNS lookup made by the agent's Node process is logged to ``netlog.jsonl`` so a
run can prove it only reached loopback services.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...clawteam.config_store import HarnessConfigStore
from ...clawteam.runtime import HarnessRunContext
from ..common import _mk_isolation_root, _safe_temp_fragment

log = logging.getLogger(__name__)

ASSETS = Path(__file__).resolve().parent / "assets"
KEEP_ISOLATION_ENV = "MASP_KEEP_QWEN_ISOLATION"
ISOLATION_ROOT_ENV = "MASP_QWEN_ISOLATION_ROOT"
ARTIFACT_DIR_ENV = "MASP_NATIVE_ARTIFACT_DIR"
NETLOG_ENV = "HA_QWEN_NETLOG"
MCP_SERVER_NAME = "multi_agent_tools"

# Harness adaptation, not task content: Qwen Code registers MCP tools under
# ``mcp__<server>__<tool>`` while ClawTeam/HarnessAudit prompts name them bare
# (``task_list``). Claude Code and Codex document their MCP naming in their own
# system prompts; this note does the same for Qwen Code. Benchmark goals, roles,
# permissions and rules are unchanged.
HARNESS_TOOL_NOTE = (
    "## Tool naming in this harness\n"
    "MCP tools must be called by their full registered names. ClawTeam coordination "
    "tools are `mcp__clawteam__<name>` (for example `task_list` is "
    "`mcp__clawteam__task_list`, `task_update` is `mcp__clawteam__task_update`, "
    "`mailbox_send` is `mcp__clawteam__mailbox_send`, `mailbox_receive` is "
    "`mcp__clawteam__mailbox_receive`). Domain tools are "
    "`mcp__multi_agent_tools__<name>`. When instructions mention a bare tool name, "
    "call the prefixed tool with the same arguments."
)

# Native Qwen Code tools that touch the file system or shell. They are excluded in
# SDE MCP-only mode (the target codebase must be reached through MCP tools).
SDE_EXCLUDED_NATIVE_TOOLS = (
    "run_shell_command", "write_file", "edit", "replace", "read_file",
    "read_many_files", "list_directory", "glob", "grep_search", "web_fetch",
    "web_search",
)


def resolve_qwen_bin(explicit: str | None = None) -> str:
    """Locate the Qwen Code CLI: explicit > HA_QWEN_BIN > PATH > default prefix."""
    candidates = [explicit, os.environ.get("HA_QWEN_BIN"), shutil.which("qwen")]
    arch = os.uname().machine
    candidates.append(
        str(Path.home() / ".local/share/harnessaudit" / f"qwen-code-{arch}" / "bin/qwen")
    )
    for cand in candidates:
        if cand and Path(cand).is_file() and os.access(cand, os.X_OK):
            return str(Path(cand))
    raise RuntimeError(
        "Qwen Code CLI not found. Install it with scripts/local_qwen/03_setup_qwen.sh "
        "--allow-download --only qwen-code, or set HA_QWEN_BIN."
    )


@dataclass
class _AgentFiles:
    role: str
    root: Path
    home: Path
    trace: Path
    stderr: Path
    netlog: Path
    mcp_config: Path
    system_prompt: Path
    route_role: str


@dataclass
class QwenCodeHarnessRuntime:
    config_store: HarnessConfigStore = field(default_factory=HarnessConfigStore)
    name: str = "qwen_code"
    _root: Path | None = field(default=None, init=False)
    _run_id: str | None = field(default=None, init=False)
    _agents: dict[str, _AgentFiles] = field(default_factory=dict, init=False)
    _hub_role: str | None = field(default=None, init=False)
    _routes: dict[str, Any] = field(default_factory=dict, init=False)
    _real_bin: str | None = field(default=None, init=False)
    _node_bin: str | None = field(default=None, init=False)
    _wrapper: Path | None = field(default=None, init=False)
    _settings_template: dict[str, Any] = field(default_factory=dict, init=False)
    _mcp_template: dict[str, Any] = field(default_factory=dict, init=False)
    _sde_mcp_only: bool = field(default=False, init=False)
    _instructions: dict[str, str] = field(default_factory=dict, init=False)
    _qwen_cfg: Any = field(default=None, init=False)

    # ---------------------------------------------------------------- setup

    def register_mcp(self, *, context: HarnessRunContext) -> None:
        from multi_agent.local_inference.config import current_config
        from multi_agent.local_inference.router import (
            is_local_only,
            require_local_route,
            resolve_route,
        )

        self.cleanup()
        cfg = current_config()
        self._qwen_cfg = cfg.harness.qwen_code
        local_only = is_local_only(cfg)
        resolver = require_local_route if local_only else resolve_route
        self._routes = {
            "target_coordinator": resolver("target_coordinator", cfg),
            "target_specialists": resolver("target_specialists", cfg),
        }
        self._real_bin = resolve_qwen_bin(cfg.harness.qwen_code.bin)
        self._node_bin = shutil.which("node") or "node"
        root = _mk_isolation_root(
            prefix=f"multi-agent-qwen-{_safe_temp_fragment(context.run_id)}-",
            root_env=ISOLATION_ROOT_ENV,
        )
        (root / "bin").mkdir(parents=True, exist_ok=True)
        wrapper = root / "bin" / "qwen"   # basename must be "qwen" for ClawTeam
        shutil.copy2(ASSETS / "qwen_wrapper.sh", wrapper)
        wrapper.chmod(0o755)

        self._root = root
        self._run_id = context.run_id
        self._wrapper = wrapper
        self._sde_mcp_only = context.sde_mcp_only
        self._hub_role = next(iter(context.worktrees), None)

        clawteam_env = {"CLAWTEAM_DATA_DIR": os.environ.get("CLAWTEAM_DATA_DIR", "")}
        clawteam_env = {k: v for k, v in clawteam_env.items() if v}
        self._mcp_template = {
            "mcpServers": {
                MCP_SERVER_NAME: {
                    "httpUrl": f"http://127.0.0.1:{context.mcp_port}/mcp",
                    "trust": True,
                    "timeout": 120000,
                },
                "clawteam": {
                    "command": context.clawteam_command,
                    "args": [],
                    "trust": True,
                    "timeout": 120000,
                    **({"env": clawteam_env} if clawteam_env else {}),
                },
            }
        }
        for role in context.worktrees:
            route_role = "target_coordinator" if role == self._hub_role else "target_specialists"
            self._agents[role] = self._prepare_agent(role, route_role)

    def _settings_for(self, route: Any) -> dict[str, Any]:
        sampling: dict[str, Any] = {"temperature": route.temperature}
        from multi_agent.local_inference.config import current_config

        inf = current_config().inference
        if getattr(inf, "top_p", None) is not None:
            sampling["top_p"] = inf.top_p
        if getattr(inf, "top_k", None) is not None:
            sampling["top_k"] = inf.top_k
        sampling["max_tokens"] = route.max_output_tokens
        return {
            "security": {"auth": {"selectedType": "openai"}},
            "privacy": {"usageStatisticsEnabled": False},
            "telemetry": {"enabled": False},
            "general": {"enableAutoUpdate": False},
            "memory": {
                "enableManagedAutoMemory": False,
                "enableManagedAutoDream": False,
                "enableAutoSkill": False,
            },
            "model": {
                "name": route.model,
                "catalogRefresh": False,
                # ClawTeam's protocol legitimately re-polls the task board / inbox;
                # Qwen Code's identical-call guard would end waiting agents early.
                # Sessions stay bounded by --max-session-turns and the agent timeout.
                "loopProtection": "off",
                "generationConfig": {
                    "samplingParams": sampling,
                    "extra_body": {
                        "chat_template_kwargs": {
                            "enable_thinking": route.reasoning_mode == "on"
                        }
                    },
                    "timeout": int(inf.request_timeout_s * 1000),
                    "maxRetries": 2,
                },
            },
            "tools": {"exclude": list(self._excluded_tools())},
        }

    def _excluded_tools(self) -> list[str]:
        excluded = list(self._qwen_cfg.exclude_tools if self._qwen_cfg else [])
        if self._sde_mcp_only:
            excluded.extend(SDE_EXCLUDED_NATIVE_TOOLS)
        return list(dict.fromkeys(excluded))

    def _prepare_agent(self, role: str, route_role: str) -> _AgentFiles:
        assert self._root is not None
        agent_root = self._root / "agents" / _safe_temp_fragment(role)
        home = agent_root / "qwen_home"
        home.mkdir(parents=True, exist_ok=True)
        route = self._routes[route_role]
        settings = self._settings_for(route)
        (home / "settings.json").write_text(json.dumps(settings, indent=2), encoding="utf-8")
        mcp_config = agent_root / "mcp.json"
        mcp_config.write_text(json.dumps(self._mcp_template, indent=2), encoding="utf-8")
        files = _AgentFiles(
            role=role,
            root=agent_root,
            home=home,
            trace=agent_root / "trace.jsonl",
            stderr=agent_root / "stderr.log",
            netlog=agent_root / "netlog.jsonl",
            mcp_config=mcp_config,
            system_prompt=agent_root / "system_prompt.md",
            route_role=route_role,
        )
        files.system_prompt.write_text(
            self._compose_system_prompt(self._instructions.get(role, "")), encoding="utf-8"
        )
        return files

    @staticmethod
    def _compose_system_prompt(role_instructions: str) -> str:
        parts = [role_instructions.strip()] if role_instructions.strip() else []
        parts.append(HARNESS_TOOL_NOTE)
        return "\n\n".join(parts)

    def register_agent_instructions(self, agent_role: str, system_prompt: str) -> None:
        """Role instructions are appended to Qwen Code's system prompt (like Claude)."""
        text = (system_prompt or "").strip()
        self._instructions[agent_role] = text
        files = self._agents.get(agent_role)
        if files is not None:
            files.system_prompt.write_text(self._compose_system_prompt(text), encoding="utf-8")

    def unregister_mcp(self) -> None:
        return None

    def build_spawn_env(self, *, agent_role: str) -> dict[str, str]:
        from multi_agent.local_inference.router import scrubbed_child_env

        if self._root is None or self._wrapper is None:
            raise RuntimeError("qwen_code runtime must register_mcp before spawning agents")
        files = self._agents.get(agent_role)
        if files is None:
            route_role = (
                "target_coordinator" if agent_role == self._hub_role else "target_specialists"
            )
            files = self._prepare_agent(agent_role, route_role)
            self._agents[agent_role] = files
        route = self._routes[files.route_role]
        env = scrubbed_child_env(dict(os.environ))
        env.update({
            "QWEN_HOME": str(files.home),
            "QWEN_CODE_SYSTEM_SETTINGS_PATH": str(files.home / "settings.json"),
            "QWEN_USAGE_STATISTICS_ENABLED": "0",
            "QWEN_CODE_SUPPRESS_YOLO_WARNING": "1",
            "QWEN_CODE_DISABLE_LSP": "1",
            "OPENAI_API_KEY": route.api_key,
            "OPENAI_BASE_URL": route.endpoint,
            "OPENAI_MODEL": route.model,
            "HA_QWEN_REAL_BIN": str(self._real_bin),
            "HA_QWEN_NODE": str(self._node_bin),
            "HA_QWEN_STAMPER": str(ASSETS / "jsonl_stamp.cjs"),
            "HA_QWEN_TRACE_FILE": str(files.trace),
            "HA_QWEN_STDERR_FILE": str(files.stderr),
            "HA_QWEN_BASE_URL": route.endpoint,
            "HA_QWEN_MODEL": route.model,
            "HA_QWEN_MCP_CONFIG": str(files.mcp_config),
            "HA_QWEN_SYSTEM_PROMPT_FILE": str(files.system_prompt),
            "CLAWTEAM_SPAWN_LOG_FILE": str(files.root / "clawteam_spawn.log"),
        })
        if os.environ.get(NETLOG_ENV, "1") != "0":
            node_opts = os.environ.get("NODE_OPTIONS", "")
            env["NODE_OPTIONS"] = f"{node_opts} --require {ASSETS / 'netlog.cjs'}".strip()
            env["HA_NETLOG"] = str(files.netlog)
        return env

    def adjust_spawn_command(self, command: list[str], *, sde_mcp_only: bool = False) -> list[str]:
        if self._wrapper is None:
            raise RuntimeError("qwen_code runtime must register_mcp before spawning agents")
        rest = list(command[1:]) if command else []
        max_turns = self._qwen_cfg.max_session_turns if self._qwen_cfg else 80
        adjusted = [
            str(self._wrapper),
            "--bare",
            "--auth-type", "openai",
            "--output-format", "stream-json",
            "--advisor", "off",
            "--max-session-turns", str(max_turns),
        ]
        excluded = self._excluded_tools() + (
            list(SDE_EXCLUDED_NATIVE_TOOLS) if sde_mcp_only else []
        )
        for tool in dict.fromkeys(excluded):   # yargs array: repeat the flag per value
            adjusted += ["--exclude-tools", tool]
        return adjusted + rest

    def asset_stage_dir(self) -> Path | None:
        return None

    def keepalive_enabled(self) -> bool:
        return False

    def session_id(self, *, run_id: str, agent_role: str) -> str | None:
        del run_id, agent_role
        return None

    # ---------------------------------------------------------------- collection

    def drain_targets(
        self, *, run_id: str, worktrees: dict[str, Path], hub_role: str
    ) -> list[Path]:
        del run_id, worktrees
        files = self._agents.get(hub_role)
        return [files.trace] if files else []

    def collect_traces(
        self,
        *,
        run_id: str,
        worktrees: dict[str, Path],
        catalog_names: set[str],
        all_roles: list[str],
        action_sink: Any,
        hub_role: str,
        spawn_timestamp: float,
        sde_mcp_only: bool = False,
    ) -> str:
        from .ingestor import collect_qwen_traces

        del worktrees, spawn_timestamp
        traces = {role: files.trace for role, files in self._agents.items()}
        final_text = collect_qwen_traces(
            traces_by_role=traces,
            catalog_tool_names=catalog_names,
            all_agent_roles=all_roles,
            action_sink=action_sink,
            hub_role=hub_role,
            run_id=run_id,
            sde_mcp_only=sde_mcp_only,
        )
        self._export_artifacts()
        return final_text

    def read_final_output(
        self,
        *,
        run_id: str,
        worktrees: dict[str, Path],
        hub_role: str,
        spawn_timestamp: float,
        quiet: bool = False,
    ) -> str:
        from .ingestor import read_final_text

        del run_id, worktrees, spawn_timestamp, quiet
        files = self._agents.get(hub_role)
        if files is None or not files.trace.exists():
            return ""
        return read_final_text(files.trace)

    def native_summary(self) -> dict[str, Any]:
        """Per-agent usage/egress summary from native artifacts (no secrets)."""
        from .ingestor import summarize_agent

        summary: dict[str, Any] = {"harness": "qwen_code", "agents": {}}
        for role, files in self._agents.items():
            summary["agents"][role] = summarize_agent(files.trace, files.netlog, files.home)
        return summary

    def _export_artifacts(self) -> None:
        target_root = os.environ.get(ARTIFACT_DIR_ENV)
        if not target_root or self._root is None or self._run_id is None:
            return
        target = Path(target_root) / self._run_id
        try:
            target.mkdir(parents=True, exist_ok=True)
            for role, files in self._agents.items():
                dest = target / _safe_temp_fragment(role)
                dest.mkdir(parents=True, exist_ok=True)
                for src in (files.trace, files.stderr, files.netlog, files.mcp_config,
                            files.system_prompt, files.home / "settings.json",
                            files.home / "usage_record.jsonl"):
                    if src.exists():
                        shutil.copy2(src, dest / src.name)
            (target / "native_summary.json").write_text(
                json.dumps(self.native_summary(), indent=2), encoding="utf-8"
            )
        except OSError:
            log.warning("Failed to export qwen_code native artifacts to %s", target,
                        exc_info=True)

    def cleanup(self) -> None:
        root = self._root
        if root and os.environ.get(KEEP_ISOLATION_ENV) != "1":
            shutil.rmtree(root, ignore_errors=True)
        self._root = None
        self._run_id = None
        self._agents = {}
        self._hub_role = None
        self._routes = {}
        self._wrapper = None
        self._sde_mcp_only = False
        self._instructions = {}
