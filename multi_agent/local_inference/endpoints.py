"""Classify model endpoints and credentials for the cost guard.

Only loopback endpoints are trusted automatically. Any other network endpoint is
``UNKNOWN`` (fail closed) until it is listed in ``inference.approved_endpoints``.
Known commercial inference hosts are always ``COMMERCIAL``, even if approved by
mistake.
"""

from __future__ import annotations

import ipaddress
from enum import Enum
from urllib.parse import urlparse


class EndpointClass(str, Enum):
    LOCAL = "LOCAL"
    SELF_HOSTED_APPROVED = "SELF_HOSTED_APPROVED"
    COMMERCIAL = "COMMERCIAL"
    UNKNOWN = "UNKNOWN"


# Suffix matches. Anything here can bill a hosted account.
COMMERCIAL_HOST_SUFFIXES: tuple[str, ...] = (
    "api.openai.com",
    "openai.azure.com",
    "cognitiveservices.azure.com",
    "api.anthropic.com",
    "openrouter.ai",
    "dashscope.aliyuncs.com",
    "dashscope-intl.aliyuncs.com",
    "dashscope-us.aliyuncs.com",
    "maas.aliyuncs.com",
    "portal.qwen.ai",
    "chat.qwen.ai",
    "generativelanguage.googleapis.com",
    "aiplatform.googleapis.com",
    "api-inference.huggingface.co",
    "router.huggingface.co",
    "inference.huggingface.co",
    "endpoints.huggingface.cloud",
    "api.together.xyz",
    "api.together.ai",
    "api.fireworks.ai",
    "api.groq.com",
    "api.deepseek.com",
    "api.mistral.ai",
    "api.cohere.com",
    "api.cohere.ai",
    "api.x.ai",
    "api.perplexity.ai",
    "api.deepinfra.com",
    "api.novita.ai",
    "api.siliconflow.cn",
    "api.moonshot.cn",
    "api.moonshot.ai",
    "amazonaws.com",
    "api.voyageai.com",
    "api.jina.ai",
)

# Values that clearly are not real credentials (unauthenticated local servers).
PLACEHOLDER_KEYS = frozenset(
    {
        "", "empty", "none", "null", "dummy", "local", "placeholder", "not-needed",
        "notneeded", "no-key", "nokey", "sk-local", "sk-no-key-required", "...",
        "token-abc123", "vllm", "ollama", "changeme",
    }
)


def is_placeholder_key(value: str | None) -> bool:
    if value is None:
        return True
    text = value.strip()
    lowered = text.lower()
    return lowered in PLACEHOLDER_KEYS or lowered.startswith("local-")


def _host_port(url: str) -> tuple[str, int | None, str]:
    parsed = urlparse(url.strip())
    return (parsed.hostname or "").lower(), parsed.port, parsed.scheme.lower()


def is_loopback_host(host: str) -> bool:
    if host in {"localhost", "localhost.localdomain", "ip6-localhost"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def is_commercial_host(host: str) -> bool:
    return any(host == sfx or host.endswith("." + sfx) for sfx in COMMERCIAL_HOST_SUFFIXES)


def _approved(host: str, port: int | None, approved: list[str]) -> bool:
    for item in approved:
        entry = item.strip().lower()
        if not entry:
            continue
        if "://" in entry:
            a_host, a_port, _ = _host_port(entry)
        elif entry.count(":") == 1:
            a_host, raw_port = entry.split(":", 1)
            a_port = int(raw_port) if raw_port.isdigit() else None
        else:
            a_host, a_port = entry, None
        if a_host == host and (a_port is None or a_port == port):
            return True
    return False


def classify_endpoint(url: str | None, approved: list[str] | None = None) -> EndpointClass:
    """Classify an OpenAI-compatible base URL."""
    if not url:
        return EndpointClass.UNKNOWN
    host, port, scheme = _host_port(url)
    if scheme not in {"http", "https"} or not host:
        return EndpointClass.UNKNOWN
    if is_commercial_host(host):
        return EndpointClass.COMMERCIAL
    if is_loopback_host(host):
        return EndpointClass.LOCAL
    if _approved(host, port, approved or []):
        return EndpointClass.SELF_HOSTED_APPROVED
    return EndpointClass.UNKNOWN


def endpoint_is_free(url: str | None, approved: list[str] | None = None) -> bool:
    return classify_endpoint(url, approved) in {
        EndpointClass.LOCAL,
        EndpointClass.SELF_HOSTED_APPROVED,
    }


# Environment variables that carry commercial credentials or endpoints. Under
# local_only these are blanked in every child process environment.
COMMERCIAL_ENV_VARS: tuple[str, ...] = (
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_API_BASE",
    "OPENAI_ORG_ID",
    "OPENAI_PROJECT",
    "OPENAI_MODEL",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "OPENROUTER_API_KEY",
    "OPENROUTER_BASE_URL",
    "DASHSCOPE_API_KEY",
    "QWEN_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GOOGLE_GENAI_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "HF_TOKEN_INFERENCE",
    "TOGETHER_API_KEY",
    "FIREWORKS_API_KEY",
    "GROQ_API_KEY",
    "DEEPSEEK_API_KEY",
    "MISTRAL_API_KEY",
    "COHERE_API_KEY",
    "XAI_API_KEY",
    "TAVILY_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_ENDPOINT",
    "AWS_BEARER_TOKEN_BEDROCK",
)
