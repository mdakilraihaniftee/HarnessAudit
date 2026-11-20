"""Model-route resolution and the cost guard used by every model-dependent path.

All call sites ask this module for a route instead of reading ``OPENAI_*``
variables directly. Under ``inference.mode = local_only`` (the default):

* every route must resolve to a loopback or explicitly approved endpoint;
* no hosted provider is ever selected, and there is no fallback;
* commercial harnesses (``claude``, ``codex``, ``openclaw``) and commercial
  attacker backends (``codex_cli``) are refused before any process starts.

``paid_opt_in`` must be selected deliberately (``HA_INFERENCE_MODE=paid_opt_in``
or ``inference.mode: paid_opt_in``); it re-enables the legacy integrations.
"""

from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass

from .config import ROLES, LocalInferenceConfig, current_config
from .endpoints import (
    COMMERCIAL_ENV_VARS,
    EndpointClass,
    classify_endpoint,
    is_placeholder_key,
)

log = logging.getLogger(__name__)

LOCAL_PLACEHOLDER_KEY = "local-no-key"

# Harnesses / backends that are known to call hosted model providers.
PAID_HARNESSES = frozenset({"claude", "codex", "openclaw"})
LOCAL_HARNESSES = frozenset({"qwen_code"})
PAID_FRAMEWORKS_WITHOUT_LOCAL_ROUTE = frozenset({"oai", "adk"})
PAID_ATTACKER_BACKENDS = frozenset({"codex_cli"})
LOCAL_ATTACKER_BACKENDS = frozenset({"qwen_code", "local_openai", "mock"})


class CostGuardError(RuntimeError):
    """Raised when a configuration could contact a billable model provider."""


@dataclass(frozen=True)
class ResolvedRoute:
    role: str
    model: str
    endpoint: str
    endpoint_class: str
    api_key: str
    api_key_source: str
    temperature: float
    max_output_tokens: int
    reasoning_mode: str
    enabled: bool
    mode: str

    @property
    def billable(self) -> bool:
        return self.endpoint_class not in {
            EndpointClass.LOCAL.value,
            EndpointClass.SELF_HOSTED_APPROVED.value,
        }

    def public_dict(self) -> dict[str, object]:
        data = asdict(self)
        data.pop("api_key", None)  # never persist key material
        data["billable"] = self.billable
        return data


def _config(config: LocalInferenceConfig | None) -> LocalInferenceConfig:
    return config or current_config()


def is_local_only(config: LocalInferenceConfig | None = None) -> bool:
    return _config(config).inference.mode == "local_only"


def resolve_route(role: str, config: LocalInferenceConfig | None = None) -> ResolvedRoute:
    """Resolve the model/endpoint for one role. Never reads ambient OPENAI_* vars."""
    cfg = _config(config)
    if role not in ROLES:
        raise ValueError(f"Unknown model role {role!r}")
    inf = cfg.inference
    route = cfg.route(role)
    endpoint = (route.endpoint or inf.endpoint).rstrip("/")
    endpoint_class = classify_endpoint(endpoint, inf.approved_endpoints)
    key_env = route.api_key_env or inf.api_key_env
    if key_env:
        api_key = os.environ.get(key_env, "")
        key_source = f"env:{key_env}" if api_key else f"env:{key_env}(unset)"
        if not api_key:
            api_key = LOCAL_PLACEHOLDER_KEY
    else:
        api_key, key_source = LOCAL_PLACEHOLDER_KEY, "placeholder"
    model = route.model or inf.served_name
    return ResolvedRoute(
        role=role,
        model=model,
        endpoint=endpoint,
        endpoint_class=endpoint_class.value,
        api_key=api_key,
        api_key_source=key_source,
        temperature=route.temperature if route.temperature is not None else inf.temperature,
        max_output_tokens=route.max_output_tokens or inf.max_output_tokens,
        reasoning_mode=route.reasoning_mode or inf.reasoning_mode,
        enabled=route.enabled,
        mode=inf.mode,
    )


def require_local_route(role: str, config: LocalInferenceConfig | None = None) -> ResolvedRoute:
    """Resolve a route and fail closed unless it is free to call."""
    route = resolve_route(role, config)
    if not route.enabled:
        raise CostGuardError(f"Model route {role!r} is disabled in the configuration")
    if route.billable:
        raise CostGuardError(
            f"Model route {role!r} resolves to {route.endpoint} "
            f"({route.endpoint_class}); local_only mode only allows loopback endpoints "
            "or endpoints listed in inference.approved_endpoints. No hosted fallback "
            "is attempted."
        )
    return route


def validate_mode(config: LocalInferenceConfig | None = None) -> None:
    cfg = _config(config)
    if cfg.inference.mode == "local_only" and cfg.inference.allow_paid_fallback:
        raise CostGuardError(
            "inference.allow_paid_fallback=true contradicts inference.mode=local_only; "
            "select paid_opt_in explicitly if you want hosted providers"
        )


def check_run_policy(
    *,
    framework: str,
    harness: str,
    model: str | None,
    skip_judge: bool,
    judge_model: str | None,
    config: LocalInferenceConfig | None = None,
) -> list[ResolvedRoute]:
    """Validate a ``multi_agent run`` configuration before any agent starts.

    Returns the resolved local routes that the run will use.
    """
    cfg = _config(config)
    validate_mode(cfg)
    if cfg.inference.mode == "paid_opt_in":
        log.warning(
            "inference.mode=paid_opt_in: hosted providers may be contacted and billed "
            "(framework=%s harness=%s model=%s judge=%s)",
            framework, harness, model, "skipped" if skip_judge else judge_model,
        )
        return []

    routes: list[ResolvedRoute] = []
    if framework in PAID_FRAMEWORKS_WITHOUT_LOCAL_ROUTE:
        raise CostGuardError(
            f"--framework {framework} uses the OpenAI SDK against hosted endpoints and "
            "has no local route yet. In local_only mode use --framework clawteam "
            "--harness qwen_code, or set HA_INFERENCE_MODE=paid_opt_in deliberately."
        )
    if harness in PAID_HARNESSES:
        raise CostGuardError(
            f"--harness {harness} launches a CLI that calls a hosted model provider. "
            "local_only mode only allows --harness qwen_code. Set "
            "HA_INFERENCE_MODE=paid_opt_in to use it deliberately."
        )
    if harness not in LOCAL_HARNESSES:
        raise CostGuardError(f"Harness {harness!r} has no verified local route")
    for role in ("target_coordinator", "target_specialists"):
        routes.append(require_local_route(role, cfg))
    served = {r.model for r in routes}
    if model and model not in served:
        raise CostGuardError(
            f"--model {model!r} is not the locally served target model "
            f"({sorted(served)}). Change routes.target_* / inference.model, or pass "
            "the served model name."
        )
    if not skip_judge:
        for role in ("completion_judge", "operational_judge"):
            judge_route = require_local_route(role, cfg)
            if judge_model and judge_model != judge_route.model:
                raise CostGuardError(
                    f"--judge-model {judge_model!r} is not the local judge route model "
                    f"{judge_route.model!r}; local_only judges must be served locally."
                )
            routes.append(judge_route)
    return routes


def check_attacker_backend(backend: str, config: LocalInferenceConfig | None = None) -> None:
    cfg = _config(config)
    validate_mode(cfg)
    if cfg.inference.mode == "paid_opt_in":
        return
    if backend in PAID_ATTACKER_BACKENDS:
        raise CostGuardError(
            f"Attacker backend {backend!r} calls a hosted provider; local_only mode "
            "allows qwen_code, local_openai, or mock. Set HA_INFERENCE_MODE=paid_opt_in "
            "deliberately to use it."
        )
    if backend not in LOCAL_ATTACKER_BACKENDS:
        raise CostGuardError(f"Attacker backend {backend!r} has no verified local route")
    if backend in {"qwen_code", "local_openai"}:
        require_local_route("attacker_generator", cfg)


def default_judge_model(config: LocalInferenceConfig | None = None) -> str:
    """Judge model used when ``--judge-model`` is omitted."""
    cfg = _config(config)
    if cfg.inference.mode == "paid_opt_in":
        return "gpt-5.4"  # legacy HarnessAudit default (paper setting)
    return resolve_route("completion_judge", cfg).model


def default_target_model(config: LocalInferenceConfig | None = None) -> str:
    cfg = _config(config)
    if cfg.inference.mode == "paid_opt_in":
        return "sonnet"  # legacy CLI default
    return resolve_route("target_specialists", cfg).model


def default_harness(config: LocalInferenceConfig | None = None) -> str:
    cfg = _config(config)
    if cfg.inference.mode == "paid_opt_in":
        return "claude"  # legacy ClawTeam default
    return cfg.harness.default


def scrubbed_child_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Blank every commercial credential/endpoint variable for a child process.

    ClawTeam starts children from ``os.environ.copy()`` and overlays our mapping,
    so blanking (not deleting) is what actually removes the values.
    """
    env = dict(base or {})
    for name in COMMERCIAL_ENV_VARS:
        env[name] = ""
    return env


def credential_presence(env: dict[str, str] | None = None) -> dict[str, str]:
    """Report which commercial credential variables are set (never their values)."""
    env_map = os.environ if env is None else env
    out: dict[str, str] = {}
    for name in COMMERCIAL_ENV_VARS:
        value = env_map.get(name)
        if value is None:
            continue
        if name.endswith(("BASE_URL", "API_BASE", "ENDPOINT")):
            cls = classify_endpoint(value)
            out[name] = f"set ({cls.value} endpoint)"
        elif is_placeholder_key(value):
            out[name] = "placeholder/empty"
        else:
            out[name] = "set (real-looking credential; value hidden)"
    return out
