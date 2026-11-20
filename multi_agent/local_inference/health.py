"""Health, structured-output and tool-calling checks for a local endpoint.

``test-tools`` exercises real OpenAI-style tool calling: the model must emit a
``tool_calls`` entry whose name exists and whose JSON arguments validate against
the declared schema, then consume the tool result in a second turn. Text that
merely *mentions* a tool does not pass.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any

FINANCE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_portfolio_summary",
            "description": "Return holdings and cash for a client portfolio.",
            "parameters": {
                "type": "object",
                "properties": {"portfolio_id": {"type": "string",
                                                "description": "Portfolio id, e.g. PF-2001"}},
                "required": ["portfolio_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_trade",
            "description": "Place a buy or sell order for a symbol in a portfolio.",
            "parameters": {
                "type": "object",
                "properties": {
                    "portfolio_id": {"type": "string"},
                    "symbol": {"type": "string"},
                    "side": {"type": "string", "enum": ["buy", "sell"]},
                    "quantity": {"type": "integer", "minimum": 1},
                },
                "required": ["portfolio_id", "symbol", "side", "quantity"],
                "additionalProperties": False,
            },
        },
    },
]


@dataclass
class CheckResult:
    name: str
    status: str            # PASS | FAIL | SKIPPED
    detail: str = ""
    latency_s: float | None = None
    data: dict[str, Any] = field(default_factory=dict)


def _validate_args(schema: dict[str, Any], args: Any) -> list[str]:
    """Minimal JSON-schema check (types, required, enum, minimum, no extras)."""
    errors: list[str] = []
    if not isinstance(args, dict):
        return [f"arguments are {type(args).__name__}, expected object"]
    props = schema.get("properties", {})
    for req in schema.get("required", []):
        if req not in args:
            errors.append(f"missing required '{req}'")
    if schema.get("additionalProperties") is False:
        extra = sorted(set(args) - set(props))
        if extra:
            errors.append(f"unexpected properties {extra}")
    types = {"string": str, "integer": int, "number": (int, float), "boolean": bool,
             "object": dict, "array": list}
    for key, value in args.items():
        prop = props.get(key)
        if not prop:
            continue
        expected = types.get(prop.get("type", ""))
        if expected and not isinstance(value, expected) or (
            prop.get("type") == "integer" and isinstance(value, bool)
        ):
            errors.append(f"'{key}' has type {type(value).__name__}, expected {prop['type']}")
        if "enum" in prop and value not in prop["enum"]:
            errors.append(f"'{key}'={value!r} not in {prop['enum']}")
        if "minimum" in prop and isinstance(value, (int, float)) and value < prop["minimum"]:
            errors.append(f"'{key}'={value} < minimum {prop['minimum']}")
    return errors


def _client(endpoint: str, api_key: str, timeout: float = 120.0) -> Any:
    from openai import OpenAI

    return OpenAI(base_url=endpoint, api_key=api_key, max_retries=0, timeout=timeout)


def check_models(endpoint: str, api_key: str, model: str) -> CheckResult:
    start = time.perf_counter()
    try:
        ids = [m.id for m in _client(endpoint, api_key, 10).models.list().data]
    except Exception as exc:  # noqa: BLE001
        return CheckResult("models", "FAIL", f"{type(exc).__name__}: {exc}")
    ok = model in ids
    return CheckResult("models", "PASS" if ok else "FAIL",
                       f"served models {ids}" + ("" if ok else f"; {model!r} missing"),
                       round(time.perf_counter() - start, 3), {"models": ids})


def check_chat(endpoint: str, api_key: str, model: str) -> CheckResult:
    start = time.perf_counter()
    try:
        resp = _client(endpoint, api_key).chat.completions.create(
            model=model, max_tokens=16, temperature=0,
            messages=[{"role": "user", "content": "Reply with exactly: LOCAL-OK"}],
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        text = (resp.choices[0].message.content or "").strip()
    except Exception as exc:  # noqa: BLE001
        return CheckResult("chat", "FAIL", f"{type(exc).__name__}: {exc}")
    return CheckResult("chat", "PASS" if "LOCAL-OK" in text else "FAIL", f"reply={text!r}",
                       round(time.perf_counter() - start, 3),
                       {"usage": resp.usage.model_dump() if resp.usage else None})


def check_structured(endpoint: str, api_key: str, model: str) -> CheckResult:
    schema = {
        "type": "object",
        "properties": {"score": {"type": "number"}, "reasoning": {"type": "string"}},
        "required": ["score", "reasoning"], "additionalProperties": False,
    }
    start = time.perf_counter()
    try:
        resp = _client(endpoint, api_key).chat.completions.create(
            model=model, max_tokens=200, temperature=0,
            messages=[{"role": "user", "content":
                       "Grade the sentence 'The trade was executed.' for completeness on a "
                       "0-1 scale. Return JSON with score and reasoning."}],
            response_format={"type": "json_schema",
                             "json_schema": {"name": "grade", "schema": schema, "strict": True}},
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        data = json.loads(resp.choices[0].message.content or "")
        errors = _validate_args(schema, data)
    except Exception as exc:  # noqa: BLE001
        return CheckResult("structured_output", "FAIL", f"{type(exc).__name__}: {exc}")
    return CheckResult("structured_output", "FAIL" if errors else "PASS",
                       "; ".join(errors) or f"parsed {data}",
                       round(time.perf_counter() - start, 3))


def check_reasoning_parser(endpoint: str, api_key: str, model: str) -> CheckResult:
    start = time.perf_counter()
    try:
        resp = _client(endpoint, api_key).chat.completions.create(
            model=model, max_tokens=4096, temperature=0.6, top_p=0.95,
            messages=[{"role": "user", "content": "What is 17*23? Answer with the number."}],
            extra_body={"chat_template_kwargs": {"enable_thinking": True}, "top_k": 20},
        )
        msg = resp.choices[0].message
        reasoning = getattr(msg, "reasoning_content", None) or getattr(msg, "reasoning", None)
        if reasoning is None and getattr(msg, "model_extra", None):
            reasoning = msg.model_extra.get("reasoning_content") or msg.model_extra.get(
                "reasoning")
        content = msg.content or ""
        finish = resp.choices[0].finish_reason
    except Exception as exc:  # noqa: BLE001
        return CheckResult("reasoning_parser", "FAIL", f"{type(exc).__name__}: {exc}")
    ok = "<think>" not in content and bool(reasoning) and "391" in content
    return CheckResult(
        "reasoning_parser", "PASS" if ok else "FAIL",
        f"content={content.strip()[:80]!r}; reasoning separated={bool(reasoning)} "
        f"({len(reasoning or '')} chars); finish={finish}",
        round(time.perf_counter() - start, 3))


def check_tool_calls(endpoint: str, api_key: str, model: str) -> CheckResult:
    client = _client(endpoint, api_key)
    schemas = {t["function"]["name"]: t["function"]["parameters"] for t in FINANCE_TOOLS}
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "You are a finance operations agent. Use tools."},
        {"role": "user", "content":
         "Look up portfolio PF-2001 with the portfolio tool, then buy 10 shares of MSFT "
         "in that portfolio."},
    ]
    start = time.perf_counter()
    details: dict[str, Any] = {"turns": []}
    try:
        resp = client.chat.completions.create(
            model=model, messages=messages, tools=FINANCE_TOOLS, tool_choice="auto",
            max_tokens=400, temperature=0,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        msg = resp.choices[0].message
        calls = msg.tool_calls or []
        if not calls:
            return CheckResult("tool_calls", "FAIL",
                               f"no structured tool_calls; content={msg.content!r}")
        problems: list[str] = []
        for call in calls:
            name = call.function.name
            if name not in schemas:
                problems.append(f"unknown tool {name!r}")
                continue
            try:
                args = json.loads(call.function.arguments)
            except json.JSONDecodeError:
                problems.append(f"{name}: arguments not JSON: {call.function.arguments!r}")
                continue
            problems += [f"{name}: {e}" for e in _validate_args(schemas[name], args)]
            details["turns"].append({"tool": name, "args": args})
        if problems:
            return CheckResult("tool_calls", "FAIL", "; ".join(problems), data=details)
        first = calls[0]
        messages.append({"role": "assistant", "content": msg.content or "",
                         "tool_calls": [c.model_dump() for c in calls]})
        for call in calls:
            result = (
                {"portfolio_id": "PF-2001", "cash": 25000.0, "positions": [["AAPL", 40]]}
                if call.function.name == "get_portfolio_summary"
                else {"status": "filled", "order_id": "ORD-1"}
            )
            messages.append({"role": "tool", "tool_call_id": call.id,
                             "content": json.dumps(result)})
        resp2 = client.chat.completions.create(
            model=model, messages=messages, tools=FINANCE_TOOLS, tool_choice="auto",
            max_tokens=400, temperature=0,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        msg2 = resp2.choices[0].message
        calls2 = msg2.tool_calls or []
        for call in calls2:
            args = json.loads(call.function.arguments)
            errs = _validate_args(schemas.get(call.function.name, {}), args)
            if errs:
                return CheckResult("tool_calls", "FAIL", f"turn2 {call.function.name}: {errs}",
                                   data=details)
            details["turns"].append({"tool": call.function.name, "args": args})
        trade_called = any(t["tool"] == "execute_trade" for t in details["turns"])
        trade_ok = any(
            t["tool"] == "execute_trade" and t["args"].get("symbol", "").upper() == "MSFT"
            and t["args"].get("side") == "buy" and t["args"].get("quantity") == 10
            and t["args"].get("portfolio_id") == "PF-2001"
            for t in details["turns"]
        )
    except Exception as exc:  # noqa: BLE001
        return CheckResult("tool_calls", "FAIL", f"{type(exc).__name__}: {exc}", data=details)
    status = "PASS" if first.function.name == "get_portfolio_summary" and trade_ok else "FAIL"
    return CheckResult(
        "tool_calls", status,
        f"sequence={[t['tool'] for t in details['turns']]}; execute_trade args correct="
        f"{trade_ok} (called={trade_called})",
        round(time.perf_counter() - start, 3), details)


def check_concurrency(endpoint: str, api_key: str, model: str, n: int = 8) -> CheckResult:
    from openai import AsyncOpenAI

    async def run() -> list[Any]:
        client = AsyncOpenAI(base_url=endpoint, api_key=api_key, max_retries=0, timeout=120)
        try:
            return await asyncio.gather(*[
                client.chat.completions.create(
                    model=model, max_tokens=24, temperature=0,
                    messages=[{"role": "user", "content": f"Reply with exactly: ACK-{i}"}],
                    extra_body={"chat_template_kwargs": {"enable_thinking": False}},
                ) for i in range(n)
            ], return_exceptions=True)
        finally:
            await client.close()

    start = time.perf_counter()
    results = asyncio.run(run())
    wall = time.perf_counter() - start
    ok = sum(
        1 for i, r in enumerate(results)
        if not isinstance(r, BaseException) and f"ACK-{i}" in (r.choices[0].message.content or "")
    )
    return CheckResult("concurrency", "PASS" if ok == n else "FAIL",
                       f"{ok}/{n} simultaneous requests answered correctly in {wall:.2f}s",
                       round(wall, 3))


def check_timeout(endpoint: str, api_key: str, model: str) -> CheckResult:
    try:
        _client(endpoint, api_key, timeout=0.05).chat.completions.create(
            model=model, max_tokens=512, messages=[{"role": "user", "content":
                                                    "Write a long essay about bonds."}],
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        return CheckResult("timeout_handling", "FAIL", "request did not time out")
    except Exception as exc:  # noqa: BLE001
        timed_out = "Timeout" in type(exc).__name__ or "timed out" in str(exc).lower()
    after = check_chat(endpoint, api_key, model)
    ok = timed_out and after.status == "PASS"
    return CheckResult("timeout_handling", "PASS" if ok else "FAIL",
                       f"client timeout raised={timed_out}; server healthy after={after.status}")


def run_checks(endpoint: str, api_key: str, model: str, *, names: list[str]) -> list[CheckResult]:
    table = {
        "models": check_models, "chat": check_chat, "structured": check_structured,
        "reasoning": check_reasoning_parser, "tools": check_tool_calls,
        "concurrency": check_concurrency, "timeout": check_timeout,
    }
    results = []
    for name in names:
        res = table[name](endpoint, api_key, model)
        results.append(res)
        if name == "models" and res.status != "PASS":
            for rest in names[1:]:
                results.append(CheckResult(rest, "SKIPPED", "endpoint not serving the model"))
            break
    return results


def results_dict(results: list[CheckResult]) -> list[dict[str, Any]]:
    return [asdict(r) for r in results]
