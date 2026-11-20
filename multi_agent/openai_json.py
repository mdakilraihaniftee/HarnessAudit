"""Shared OpenAI SDK helpers for JSON-only model calls."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import time
from importlib import import_module
from typing import Any


class OpenAIJSONError(RuntimeError):
    """Raised when the OpenAI SDK is unavailable or returns invalid JSON."""


class LocalEndpointUnavailable(RuntimeError):
    """The local model server for a judge route could not be reached.

    Deliberately *not* an ``OpenAIJSONError``: judge callers swallow that type and
    score 0.0, which would hide a missing local server. This propagates instead,
    and no hosted provider is tried.
    """


log = logging.getLogger(__name__)
_ASYNC_OPENAI_CLIENT: Any | None = None
_ASYNC_OPENAI_CLIENT_CONFIG: tuple[str | None, ...] | None = None

_TRANSIENT_STATUS_CODES = {408, 409, 429, 500, 502, 503, 504}
_TRANSIENT_ERROR_NAMES = {
    "APIConnectionError",
    "APITimeoutError",
    "ConnectError",
    "ConnectionTimeout",
    "ReadTimeout",
    "Timeout",
}


JUDGE_PROVIDER_ENV = "MASP_JUDGE_PROVIDER"
OPENROUTER_DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"


def _client_kwargs() -> dict[str, str]:
    """Credentials for the JSON judge client.

    Default (``MASP_JUDGE_PROVIDER`` unset or ``openai``): the SDK reads OPENAI_API_KEY /
    OPENAI_BASE_URL itself. With ``MASP_JUDGE_PROVIDER=openrouter`` only the judge uses
    OPENROUTER_API_KEY / OPENROUTER_BASE_URL, so OPENAI_* stays untouched for the agents
    (e.g. Codex in api_key mode).
    """

    provider = os.getenv(JUDGE_PROVIDER_ENV, "").strip().lower()
    if provider in {"", "openai"}:
        return {}
    if provider == "openrouter":
        api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
        if not api_key or api_key == "...":
            raise OpenAIJSONError(
                f"{JUDGE_PROVIDER_ENV}=openrouter requires OPENROUTER_API_KEY to be set"
            )
        base_url = os.getenv("OPENROUTER_BASE_URL", "").strip() or OPENROUTER_DEFAULT_BASE_URL
        return {"api_key": api_key, "base_url": base_url}
    raise OpenAIJSONError(
        f"Unsupported {JUDGE_PROVIDER_ENV}={provider!r}; expected 'openai' or 'openrouter'"
    )


def _retry_attempts() -> int:
    raw = os.getenv("MASP_OPENAI_RETRY_ATTEMPTS", "3")
    try:
        return max(1, int(raw))
    except ValueError:
        return 3


def _retry_base_delay() -> float:
    raw = os.getenv("MASP_OPENAI_RETRY_BASE_DELAY", "1.0")
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 1.0


def _status_code(exc: BaseException) -> int | None:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status

    response = getattr(exc, "response", None)
    response_status = getattr(response, "status_code", None)
    return response_status if isinstance(response_status, int) else None


def _is_transient_openai_error(exc: BaseException) -> bool:
    status = _status_code(exc)
    if status in _TRANSIENT_STATUS_CODES:
        return True
    return exc.__class__.__name__ in _TRANSIENT_ERROR_NAMES


def _request_error_message(exc: BaseException) -> str:
    status = _status_code(exc)
    status_part = f" status={status}" if status is not None else ""
    return f"OpenAI request failed{status_part}: {exc}"


def _is_unsupported_parameter_error(exc: BaseException, parameter: str) -> bool:
    """Return whether the API explicitly rejected a request parameter."""

    if _status_code(exc) != 400:
        return False
    error_param = getattr(exc, "param", None)
    if error_param == parameter:
        return True
    message = str(exc).lower()
    return parameter.lower() in message and "unsupported parameter" in message


def _retry_delay(base_delay: float, attempt: int) -> float:
    return base_delay * (2 ** max(0, attempt - 1))


def _extract_output_text(response: Any) -> str:
    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str) and output_text.strip():
        return output_text

    output = getattr(response, "output", None)
    if isinstance(output, list):
        parts: list[str] = []
        for item in output:
            content = getattr(item, "content", None)
            if content is None and isinstance(item, dict):
                content = item.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                text = getattr(block, "text", None)
                if text is None and isinstance(block, dict):
                    text = block.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
        if parts:
            return "".join(parts)

    raise OpenAIJSONError("OpenAI response did not contain output text")


def _parse_json_response(response: Any) -> Any:
    content = _extract_output_text(response)
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise OpenAIJSONError(f"Model returned non-JSON content: {content[:500]}") from exc


def _close_sync_client(client: Any) -> None:
    close = getattr(client, "close", None)
    if not callable(close):
        return
    try:
        close()
    except Exception as exc:  # noqa: BLE001
        log.debug("Failed to close OpenAI client cleanly: %s", exc)


async def _close_async_client(client: Any) -> None:
    close = getattr(client, "close", None)
    if not callable(close):
        close = getattr(client, "aclose", None)
    if not callable(close):
        return
    try:
        result = close()
        if inspect.isawaitable(result):
            await result
    except Exception as exc:  # noqa: BLE001
        log.debug("Failed to close async OpenAI client cleanly: %s", exc)


async def _get_async_openai_client() -> Any:
    """Return a process-local async OpenAI client for JSON judge calls."""

    global _ASYNC_OPENAI_CLIENT, _ASYNC_OPENAI_CLIENT_CONFIG

    openai = import_module("openai")
    client_kwargs = _client_kwargs()
    config = (
        os.getenv(JUDGE_PROVIDER_ENV),
        client_kwargs.get("api_key"),
        client_kwargs.get("base_url"),
        os.getenv("OPENAI_API_KEY"),
        os.getenv("OPENAI_BASE_URL"),
        os.getenv("OPENAI_ORG_ID"),
        os.getenv("OPENAI_PROJECT"),
        str(id(asyncio.get_running_loop())),
    )
    if _ASYNC_OPENAI_CLIENT is None or _ASYNC_OPENAI_CLIENT_CONFIG != config:
        if _ASYNC_OPENAI_CLIENT is not None:
            await _close_async_client(_ASYNC_OPENAI_CLIENT)
        _ASYNC_OPENAI_CLIENT = openai.AsyncOpenAI(**client_kwargs)
        _ASYNC_OPENAI_CLIENT_CONFIG = config
    return _ASYNC_OPENAI_CLIENT


# ----------------------------------------------------------------- local route


def _local_route(role: str, model: str) -> Any | None:
    """Return the local route for ``role`` in local_only mode, else ``None``.

    Raises ``CostGuardError`` when the route is not free to call or when the
    caller asks for a model the local route does not serve (no substitution).
    """
    from multi_agent.local_inference.router import (
        CostGuardError,
        is_local_only,
        require_local_route,
    )

    if not is_local_only():
        return None
    route = require_local_route(role)
    if model and model != route.model:
        raise CostGuardError(
            f"Judge model {model!r} is not served by the local {role} route "
            f"({route.model!r} at {route.endpoint}). local_only mode never substitutes "
            "or falls back to a hosted judge; pass --judge-model "
            f"{route.model} or configure routes.{role}.model."
        )
    return route


def _strip_to_json(text: str) -> str:
    content = text.strip()
    if "</think>" in content:  # reasoning text leaked into content
        content = content.split("</think>", 1)[1].strip()
    if content.startswith("```"):
        content = content.strip("`")
        if content.lower().startswith("json"):
            content = content[4:]
    start, end = content.find("{"), content.rfind("}")
    if start != -1 and end > start:
        return content[start:end + 1]
    return content


def _local_request(route: Any, system_prompt: str, user_prompt: str,
                   max_output_tokens: int) -> dict[str, Any]:
    return {
        "model": route.model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": route.temperature,
        "max_tokens": min(max_output_tokens, route.max_output_tokens),
        "response_format": {"type": "json_object"},
        "extra_body": {
            "chat_template_kwargs": {"enable_thinking": route.reasoning_mode == "on"}
        },
    }


def _parse_local(response: Any, route: Any) -> Any:
    from multi_agent.local_inference.performance import record_request

    choice = response.choices[0]
    text = choice.message.content or ""
    usage = getattr(response, "usage", None)
    record_request(route.role, route.model, usage, finish_reason=choice.finish_reason)
    try:
        return json.loads(_strip_to_json(text))
    except json.JSONDecodeError as exc:
        raise OpenAIJSONError(
            f"Local judge returned non-JSON content (finish={choice.finish_reason}): "
            f"{text[:500]}"
        ) from exc


def _local_unavailable(route: Any, exc: BaseException) -> LocalEndpointUnavailable:
    return LocalEndpointUnavailable(
        f"Local {route.role} endpoint {route.endpoint} is unavailable ({exc}). Start it "
        "with `python -m multi_agent.local_inference serve` or pass --skip-judge. "
        "No hosted fallback was attempted."
    )


def _create_local_json_sync(route: Any, system_prompt: str, user_prompt: str,
                            max_output_tokens: int) -> Any:
    openai = import_module("openai")
    client = openai.OpenAI(base_url=route.endpoint, api_key=route.api_key, max_retries=0,
                           timeout=_local_timeout())
    try:
        last: BaseException | None = None
        for attempt in range(1, _retry_attempts() + 1):
            try:
                response = client.chat.completions.create(
                    **_local_request(route, system_prompt, user_prompt, max_output_tokens)
                )
                return _parse_local(response, route)
            except OpenAIJSONError:
                raise
            except Exception as exc:  # noqa: BLE001
                last = exc
                if _is_transient_openai_error(exc) and attempt < _retry_attempts():
                    time.sleep(_retry_delay(_retry_base_delay(), attempt))
                    continue
                break
        assert last is not None
        if _is_transient_openai_error(last):
            raise _local_unavailable(route, last) from last
        raise OpenAIJSONError(f"Local judge request failed: {last}") from last
    finally:
        _close_sync_client(client)


async def _create_local_json_async(route: Any, system_prompt: str, user_prompt: str,
                                   max_output_tokens: int) -> Any:
    openai = import_module("openai")
    client = openai.AsyncOpenAI(base_url=route.endpoint, api_key=route.api_key,
                                max_retries=0, timeout=_local_timeout())
    try:
        last: BaseException | None = None
        for attempt in range(1, _retry_attempts() + 1):
            try:
                response = await client.chat.completions.create(
                    **_local_request(route, system_prompt, user_prompt, max_output_tokens)
                )
                return _parse_local(response, route)
            except OpenAIJSONError:
                raise
            except Exception as exc:  # noqa: BLE001
                last = exc
                if _is_transient_openai_error(exc) and attempt < _retry_attempts():
                    await asyncio.sleep(_retry_delay(_retry_base_delay(), attempt))
                    continue
                break
        assert last is not None
        if _is_transient_openai_error(last):
            raise _local_unavailable(route, last) from last
        raise OpenAIJSONError(f"Local judge request failed: {last}") from last
    finally:
        await _close_async_client(client)


def _local_timeout() -> float:
    from multi_agent.local_inference.config import current_config

    return float(current_config().inference.request_timeout_s)


def create_json_response(
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    max_output_tokens: int = 1024,
    role: str = "completion_judge",
) -> Any:
    route = _local_route(role, model)
    if route is not None:
        return _create_local_json_sync(route, system_prompt, user_prompt, max_output_tokens)
    client_kwargs = _client_kwargs()
    try:
        openai = import_module("openai")
        client = openai.OpenAI(**client_kwargs)
    except Exception as exc:  # noqa: BLE001
        raise OpenAIJSONError(
            "OpenAI SDK is required for non-dry-run generation. "
            "Install the optional oai dependencies and configure OpenAI credentials."
        ) from exc

    attempts = _retry_attempts()
    base_delay = _retry_base_delay()
    try:
        for attempt in range(1, attempts + 1):
            try:
                request: dict[str, Any] = {
                    "model": model,
                    "instructions": system_prompt,
                    "input": user_prompt,
                    "max_output_tokens": max_output_tokens,
                    "temperature": 0,
                }
                response = client.responses.create(**request)
                break
            except Exception as exc:  # noqa: BLE001
                if _is_unsupported_parameter_error(exc, "temperature"):
                    try:
                        response = client.responses.create(
                            model=model,
                            instructions=system_prompt,
                            input=user_prompt,
                            max_output_tokens=max_output_tokens,
                        )
                    except Exception as fallback_exc:  # noqa: BLE001
                        exc = fallback_exc
                    else:
                        break
                if attempt < attempts and _is_transient_openai_error(exc):
                    delay = _retry_delay(base_delay, attempt)
                    log.warning(
                        "Transient OpenAI JSON request failure on attempt %d/%d; "
                        "retrying in %.1fs: %s",
                        attempt,
                        attempts,
                        delay,
                        exc,
                    )
                    if delay:
                        time.sleep(delay)
                    continue
                raise OpenAIJSONError(_request_error_message(exc)) from exc

        return _parse_json_response(response)
    finally:
        _close_sync_client(client)


async def create_json_response_async(
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    max_output_tokens: int = 1024,
    role: str = "completion_judge",
) -> Any:
    route = _local_route(role, model)
    if route is not None:
        return await _create_local_json_async(
            route, system_prompt, user_prompt, max_output_tokens
        )
    try:
        client = await _get_async_openai_client()
    except OpenAIJSONError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise OpenAIJSONError(
            "OpenAI SDK is required for LLM-as-judge scoring. "
            "Install the optional oai dependencies and configure OpenAI credentials."
        ) from exc

    attempts = _retry_attempts()
    base_delay = _retry_base_delay()
    for attempt in range(1, attempts + 1):
        try:
            request: dict[str, Any] = {
                "model": model,
                "instructions": system_prompt,
                "input": user_prompt,
                "max_output_tokens": max_output_tokens,
                "temperature": 0,
            }
            response = await client.responses.create(**request)
            break
        except Exception as exc:  # noqa: BLE001
            if _is_unsupported_parameter_error(exc, "temperature"):
                try:
                    response = await client.responses.create(
                        model=model,
                        instructions=system_prompt,
                        input=user_prompt,
                        max_output_tokens=max_output_tokens,
                    )
                except Exception as fallback_exc:  # noqa: BLE001
                    exc = fallback_exc
                else:
                    break
            if attempt < attempts and _is_transient_openai_error(exc):
                delay = _retry_delay(base_delay, attempt)
                log.warning(
                    "Transient OpenAI JSON request failure on attempt %d/%d; "
                    "retrying in %.1fs: %s",
                    attempt,
                    attempts,
                    delay,
                    exc,
                )
                if delay:
                    await asyncio.sleep(delay)
                continue
            raise OpenAIJSONError(_request_error_message(exc)) from exc
    return _parse_json_response(response)
