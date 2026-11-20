"""Local (open-weight) inference: cost guard, model routes, GPU placement, servers.

Fully local inference is the default (``inference.mode = local_only``). See
docs/LOCAL_QWEN_SETUP.md and ``python -m multi_agent.local_inference --help``.
"""

from .config import LocalInferenceConfig, current_config, inference_mode, load_config
from .router import (
    CostGuardError,
    ResolvedRoute,
    check_attacker_backend,
    check_run_policy,
    is_local_only,
    require_local_route,
    resolve_route,
)

__all__ = [
    "CostGuardError",
    "LocalInferenceConfig",
    "ResolvedRoute",
    "check_attacker_backend",
    "check_run_policy",
    "current_config",
    "inference_mode",
    "is_local_only",
    "load_config",
    "require_local_route",
    "resolve_route",
]
