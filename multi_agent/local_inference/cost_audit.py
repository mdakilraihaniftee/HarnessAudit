"""Cost audit: every model-dependent path and whether the current config can bill.

``CALL_PATHS`` is the static inventory from the repository audit (see
docs/LOCAL_INFERENCE_COST_AUDIT.md). :func:`build_report` combines it with the
*live* resolution of the effective configuration, and :func:`verify` turns that
into an exit code for ``python -m multi_agent.local_inference verify-cost``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import asdict, dataclass
from typing import Any

from .config import ROLES, LocalInferenceConfig
from .endpoints import EndpointClass
from .router import (
    LOCAL_ATTACKER_BACKENDS,
    LOCAL_HARNESSES,
    PAID_ATTACKER_BACKENDS,
    PAID_HARNESSES,
    CostGuardError,
    credential_presence,
    resolve_route,
    validate_mode,
)

LOCAL_ONLY, PAID_OPT_IN, NON_MODEL, UNKNOWN = "LOCAL_ONLY", "PAID_OPT_IN", "NON_MODEL", "UNKNOWN"


@dataclass(frozen=True)
class CallPath:
    id: str
    source: str
    purpose: str
    legacy_model: str
    provider: str
    can_bill: str
    auth: str
    silent_fallback: str
    local_replacement: str
    status: str
    classification: str


CALL_PATHS: tuple[CallPath, ...] = (
    CallPath("judge.completion", "multi_agent/completion_judge.py::_evaluate_llm_checkpoint "
             "-> openai_json.create_json_response_async", "Pooled LLM task-completion score",
             "gpt-5.4 (CLI default)", "OpenAI Responses API, or OpenRouter via "
             "MASP_JUDGE_PROVIDER=openrouter", "yes (legacy)", "OPENAI_API_KEY / "
             "OPENROUTER_API_KEY", "no fallback, but the legacy *default* was hosted",
             "routes.completion_judge -> local vLLM (chat.completions + JSON mode)",
             "migrated; hosted only in paid_opt_in", LOCAL_ONLY),
    CallPath("judge.operational", "multi_agent/operational_judge.py::evaluate_operational_"
             "governance -> openai_json", "L2 action-validity (AVS) score", "gpt-5.4",
             "OpenAI / OpenRouter", "yes (legacy)", "OPENAI_API_KEY / OPENROUTER_API_KEY",
             "same as above", "routes.operational_judge -> local vLLM",
             "migrated; hosted only in paid_opt_in", LOCAL_ONLY),
    CallPath("judge.perturbation", "multi_agent/perturbation_eval.py::_llm_subscores -> "
             "openai_json", "Perturbation stability sub-scores", "gpt-5.4",
             "OpenAI / OpenRouter", "yes (legacy)", "OPENAI_API_KEY / OPENROUTER_API_KEY",
             "on failure returns heuristic defaults (no provider fallback)",
             "routes.perturbation_judge -> local vLLM", "migrated", LOCAL_ONLY),
    CallPath("judge.probe_scripts", "scripts/probe_qwen_judge.py, scripts/run_qwen_judge.py",
             "OpenRouter-hosted Qwen judge demos", "qwen/qwen3-8b @ OpenRouter", "OpenRouter",
             "yes", "OPENROUTER_API_KEY", "no", "python -m multi_agent.local_inference health "
             "(local judge probe)", "kept; refused under local_only by the cost guard",
             PAID_OPT_IN),
    CallPath("harness.claude", "multi_agent/frameworks/native/claude/runtime.py (ClawTeam "
             "--harness claude)", "Target agents via Claude Code CLI", "sonnet (CLI default)",
             "Anthropic API or Claude login", "yes", "ANTHROPIC_API_KEY / claude login",
             "CLI may use cached login when no key is set", "--harness qwen_code",
             "kept; refused under local_only", PAID_OPT_IN),
    CallPath("harness.codex", "multi_agent/frameworks/native/codex/runtime.py (--harness "
             "codex)", "Target agents via `codex exec`", "gpt-5.x", "OpenAI API or ChatGPT "
             "login", "yes", "OPENAI_API_KEY / ~/.codex auth.json",
             "auto-detects api_key vs cli_login", "--harness qwen_code",
             "kept; refused under local_only", PAID_OPT_IN),
    CallPath("harness.openclaw", "multi_agent/frameworks/native/openclaw/* (--harness "
             "openclaw, single_agent openclaw_local)", "Target agents via OpenClaw",
             "gpt-5.4 / claude-* via OpenRouter", "OpenRouter", "yes", "OPENROUTER_API_KEY",
             "no", "--harness qwen_code", "kept; refused under local_only", PAID_OPT_IN),
    CallPath("harness.qwen_code", "multi_agent/frameworks/native/qwen_code/* (--harness "
             "qwen_code)", "Target coordinator + specialists via Qwen Code CLI",
             "n/a (new)", "local vLLM (OpenAI-compatible)", "no", "placeholder key only",
             "none: --auth-type openai + explicit --openai-base-url; isolated QWEN_HOME; "
             "--bare; commercial env vars blanked; no --fallback-model",
             "this is the replacement", "new default harness", LOCAL_ONLY),
    CallPath("framework.oai", "multi_agent/frameworks/oai/adapter.py", "OpenAI Agents SDK "
             "multi-agent runtime", "gpt-* / litellm anthropic|gemini", "OpenAI, Anthropic, "
             "Gemini (litellm)", "yes", "OPENAI_API_KEY (+ provider keys)",
             "model-name heuristics route claude/gemini names to litellm providers",
             "not migrated (use clawteam + qwen_code)", "refused under local_only",
             PAID_OPT_IN),
    CallPath("framework.adk", "multi_agent/frameworks/adk/*", "Google ADK runtime over the "
             "OpenAI SDK", "gpt-*", "OpenAI (OPENAI_BASE_URL)", "yes", "OPENAI_API_KEY", "no",
             "not migrated (use clawteam + qwen_code)", "refused under local_only",
             PAID_OPT_IN),
    CallPath("attacker.codex_cli", "multi_agent/attacks/agent.py::CodexCliBackend",
             "SI-ATG candidate generation via `codex exec`", "Codex default / --model",
             "OpenAI via Codex CLI", "yes", "Codex login / OPENAI_API_KEY", "no",
             "attacker.qwen_code or attacker.local_openai", "kept; refused under local_only",
             PAID_OPT_IN),
    CallPath("attacker.qwen_code", "multi_agent/attacks/qwen_backend.py::"
             "QwenCodeAttackerBackend", "SI-ATG generation via headless Qwen Code "
             "(--json-schema)", "n/a (new)", "local vLLM", "no", "placeholder", "none",
             "this is the replacement", "new", LOCAL_ONLY),
    CallPath("attacker.local_openai", "multi_agent/attacks/qwen_backend.py::"
             "LocalOpenAIAttackerBackend", "SI-ATG generation with server-side JSON-schema "
             "decoding", "n/a (new)", "local vLLM", "no", "placeholder", "none",
             "alternative replacement", "new", LOCAL_ONLY),
    CallPath("attacker.reflector", "multi_agent/attacks/agent.py::AttackerAgent._reflect",
             "Post-round reflection text", "none", "deterministic code", "no", "none", "n/a",
             "n/a (route attacker_reflector reserved for future LLM reflection)",
             "unchanged", NON_MODEL),
    CallPath("attacker.harness_optimizer", "multi_agent/attacks/agent.py::"
             "AttackerAgent._optimize_harness", "Strategy-weight promotion with held-out "
             "check", "none", "deterministic code", "no", "none", "n/a",
             "n/a (route reserved)", "unchanged", NON_MODEL),
    CallPath("attacker.mock_control", "multi_agent/attacks/agent.py::MockBackend, "
             "ControlBackend, StrategySelector", "Deterministic demos / fixed controls B1-B2",
             "none", "none", "no", "none", "n/a", "n/a", "unchanged", NON_MODEL),
    CallPath("telemetry.openai_agents", "frameworks/oai/adapter.py set_tracing_disabled(True)",
             "Agents SDK trace export", "n/a", "api.openai.com (traces)", "no (not inference)",
             "OPENAI_API_KEY", "n/a", "already disabled", "unchanged", NON_MODEL),
    CallPath("telemetry.qwen_code", "Qwen Code usage statistics, auto-update, model-catalog "
             "refresh, managed auto-memory/dream/skill", "CLI background features",
             "n/a", "Alibaba endpoints / local model", "no (not billable; network egress)",
             "none", "n/a", "disabled: QWEN_USAGE_STATISTICS_ENABLED=0, settings, --bare, "
             "--advisor off", "disabled", NON_MODEL),
    CallPath("telemetry.vllm", "vLLM usage stats", "server start-up telemetry", "n/a",
             "vLLM stats endpoint", "no", "none", "n/a", "VLLM_NO_USAGE_STATS=1, DO_NOT_TRACK=1",
             "disabled", NON_MODEL),
    CallPath("download.hf_weights", "scripts/local_qwen/03_setup_qwen.sh; vLLM at start-up",
             "Model weight download", "n/a", "huggingface.co", "no (not inference)",
             "optional HF_TOKEN", "vLLM would download a missing model",
             "setup needs --allow-download; server runs with HF_HUB_OFFLINE=1 unless "
             "inference.allow_model_download=true", "gated", NON_MODEL),
    CallPath("scripts.legacy_runners", "multi_agent/run_ma.sh, rc/project.env SMOKE/FULL, "
             "scripts/run_codex_benchmark.py, si_atg_experiment_scripts/04_*",
             "Batch runners that default to hosted harnesses", "gpt-5.4 / gpt-5.6-sol",
             "OpenAI / OpenRouter", "yes (legacy)", "API keys", "no",
             "scripts/local_qwen/*; run_ma.sh defaults to qwen_code under local_only",
             "kept; every run goes through the CLI cost guard", PAID_OPT_IN),
)


@dataclass
class Finding:
    level: str      # OK | WARN | BILLABLE | UNRESOLVED
    item: str
    detail: str


def _qwen_code_info(cfg: LocalInferenceConfig) -> dict[str, Any]:
    try:
        from multi_agent.frameworks.native.qwen_code.runtime import resolve_qwen_bin

        binary = resolve_qwen_bin(cfg.harness.qwen_code.bin)
    except RuntimeError as exc:
        return {"found": False, "error": str(exc)}
    try:
        version = subprocess.run([binary, "--version"], capture_output=True, text=True,
                                 timeout=60).stdout.strip().splitlines()[-1]
    except (OSError, subprocess.SubprocessError, IndexError):
        version = None
    return {"found": True, "bin": binary, "version": version, "node": shutil.which("node")}


def _probe(endpoint: str, model: str) -> tuple[bool, str]:
    from .server import _http_ok

    ok, body = _http_ok(endpoint.rstrip("/") + "/models", timeout=5)
    if not ok:
        return False, f"unreachable ({body[:120]})"
    if model not in body:
        return False, f"reachable but does not serve {model!r}"
    return True, "reachable; serves model"


def build_report(cfg: LocalInferenceConfig, *, probe: bool = True,
                 attacker_backend: str | None = None,
                 harness: str | None = None) -> dict[str, Any]:
    findings: list[Finding] = []
    mode = cfg.inference.mode
    try:
        validate_mode(cfg)
    except CostGuardError as exc:
        findings.append(Finding("BILLABLE", "inference.mode", str(exc)))

    routes = []
    probed: dict[tuple[str, str], tuple[bool, str]] = {}
    for role in ROLES:
        route = resolve_route(role, cfg)
        row = route.public_dict()
        if not route.enabled:
            row["probe"] = "route disabled"
            routes.append(row)
            continue
        if route.endpoint_class == EndpointClass.COMMERCIAL.value:
            level = "BILLABLE" if mode == "local_only" else "WARN"
            findings.append(Finding(level, f"route.{role}",
                                    f"{route.endpoint} is a commercial endpoint"))
        elif route.endpoint_class == EndpointClass.UNKNOWN.value:
            level = "BILLABLE" if mode == "local_only" else "WARN"
            findings.append(Finding(
                level, f"route.{role}",
                f"{route.endpoint} is not loopback and not in inference.approved_endpoints "
                "(treated as potentially billable until approved)"))
        if probe and not route.billable:
            key = (route.endpoint, route.model)
            if key not in probed:
                probed[key] = _probe(route.endpoint, route.model)
            ok, msg = probed[key]
            row["probe"] = msg
            if not ok:
                findings.append(Finding("UNRESOLVED", f"route.{role}",
                                        f"{route.endpoint}: {msg}"))
        routes.append(row)

    default_harness = harness or (cfg.harness.default if mode == "local_only" else "claude")
    if mode == "local_only":
        if default_harness in PAID_HARNESSES:
            findings.append(Finding("BILLABLE", "harness", f"{default_harness} is hosted"))
        elif default_harness not in LOCAL_HARNESSES:
            findings.append(Finding("UNRESOLVED", "harness",
                                    f"{default_harness} has no verified local route"))
    if attacker_backend:
        if mode == "local_only" and attacker_backend in PAID_ATTACKER_BACKENDS:
            findings.append(Finding("BILLABLE", "attacker_backend", attacker_backend))
        elif attacker_backend not in LOCAL_ATTACKER_BACKENDS | PAID_ATTACKER_BACKENDS:
            findings.append(Finding("UNRESOLVED", "attacker_backend", attacker_backend))

    qwen = _qwen_code_info(cfg)
    if default_harness == "qwen_code" and not qwen.get("found"):
        findings.append(Finding("UNRESOLVED", "qwen_code", qwen.get("error", "not found")))
    creds = credential_presence()
    if creds and mode == "local_only":
        findings.append(Finding(
            "OK", "credentials",
            "commercial credential variables are present in the environment but are not "
            "used by any local route and are blanked for agent subprocesses: "
            + ", ".join(sorted(creds))))
    if mode == "paid_opt_in":
        findings.append(Finding("WARN", "inference.mode",
                                "paid_opt_in is active: hosted providers may be billed"))

    levels = {f.level for f in findings}
    if mode == "paid_opt_in":
        verdict = "PAID_OPT_IN_ACTIVE"
    elif "BILLABLE" in levels:
        verdict = "POTENTIALLY_BILLABLE"
    elif "UNRESOLVED" in levels:
        verdict = "UNRESOLVED"
    else:
        verdict = "LOCAL_ONLY_VERIFIED" if probe else "LOCAL_ONLY_STATIC_OK"
    return {
        "verdict": verdict,
        "can_incur_model_api_charges": verdict not in {"LOCAL_ONLY_VERIFIED",
                                                       "LOCAL_ONLY_STATIC_OK",
                                                       "UNRESOLVED"},
        "mode": mode,
        "config_sources": cfg.sources,
        "default_harness": default_harness,
        "attacker_backend": attacker_backend,
        "routes": routes,
        "qwen_code": qwen,
        "credentials_present": creds,
        "findings": [asdict(f) for f in findings],
        "call_paths": [asdict(p) for p in CALL_PATHS],
        "env": {"HA_INFERENCE_MODE": os.environ.get("HA_INFERENCE_MODE"),
                "MASP_JUDGE_PROVIDER": os.environ.get("MASP_JUDGE_PROVIDER")},
    }


EXIT_CODES = {
    "LOCAL_ONLY_VERIFIED": 0,
    "LOCAL_ONLY_STATIC_OK": 0,
    "POTENTIALLY_BILLABLE": 2,
    "UNRESOLVED": 3,
    "PAID_OPT_IN_ACTIVE": 4,
}


def format_report(report: dict[str, Any], *, show_paths: bool = True) -> str:
    lines = [
        f"inference mode : {report['mode']}",
        f"verdict        : {report['verdict']}  (model API charges possible: "
        f"{'YES' if report['can_incur_model_api_charges'] else 'no'})",
        f"config sources : {', '.join(report['config_sources'])}",
        f"default harness: {report['default_harness']}",
    ]
    if report.get("attacker_backend"):
        lines.append(f"attacker       : {report['attacker_backend']}")
    q = report["qwen_code"]
    lines.append(f"qwen code      : {q.get('bin')} {q.get('version')}" if q.get("found")
                 else f"qwen code      : NOT FOUND ({q.get('error')})")
    lines.append("routes:")
    for r in report["routes"]:
        lines.append(
            f"  {r['role']:<27} {r['model']:<22} {r['endpoint']:<32} {r['endpoint_class']:<20}"
            f" billable={'yes' if r['billable'] else 'no'}  {r.get('probe', '')}"
        )
    if report["findings"]:
        lines.append("findings:")
        for f in report["findings"]:
            lines.append(f"  [{f['level']}] {f['item']}: {f['detail']}")
    if show_paths:
        lines.append("model-dependent call paths:")
        for p in report["call_paths"]:
            lines.append(f"  {p['classification']:<12} {p['id']:<28} {p['status']}")
    return "\n".join(lines)
