"""Phase 1R-only finance MCP service, with harness-bound actor metadata."""

from __future__ import annotations

import argparse
import inspect
import json
import signal
import socket
import sys
from pathlib import Path

from mcp.server.fastmcp import Context, FastMCP

from multi_agent.banks import get_bank
from multi_agent.loader import load_tool_catalog
from multi_agent.schemas.task import TaskConfig

from .audit import write
from .effects import EffectLedger, completion, snapshot


def build_server(spec, *, port=0):
    task = TaskConfig.model_validate(spec["task"])
    if task.task_id != "fin-t1" or task.domain != "finance":
        raise ValueError("Phase 1R instrumentation is scoped to fin-t1")
    bank = get_bank("finance")
    ledger = EffectLedger(bank, task, spec["evidence_dir"], spec["trial_id"])
    write(ledger.root / "final_state.json", snapshot(bank.conn))
    ledger._db("final.sqlite")
    write(ledger.root / "effect_ledger.json", [])
    write(ledger.root / "resource_access_ledger.json", [])
    write(
        ledger.root / "deterministic_completion.json",
        completion(task, ledger.initial, ledger.initial, []),
    )
    mcp = FastMCP("multi_agent_tools", host="127.0.0.1", port=port)
    capabilities = spec["capabilities"]
    for td in load_tool_catalog("finance").tools:

        def make_handler(tool):
            async def handler(ctx: Context, **kwargs):
                req = ctx.request_context.request
                token = req.query_params.get("identity") if req is not None else None
                if token not in capabilities:
                    raise ValueError("Missing harness-bound actor capability")
                role = capabilities[token]
                return ledger.invoke(role, tool.name, kwargs)

            params = [
                inspect.Parameter(k, inspect.Parameter.KEYWORD_ONLY, default="", annotation=str)
                for k in tool.params
            ]
            params.append(
                inspect.Parameter(
                    "ctx", inspect.Parameter.KEYWORD_ONLY, default=None, annotation=Context
                )
            )
            handler.__signature__ = inspect.Signature(params, return_annotation=str)
            handler.__name__ = tool.name
            handler.__doc__ = tool.description or tool.name
            return handler

        mcp.tool()(make_handler(td))
    return mcp, bank, ledger


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--spec", type=Path, required=True)
    p.add_argument("--bank-dump-path", type=Path, required=True)
    a = p.parse_args()
    spec = json.loads(a.spec.read_text())
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    mcp, bank, ledger = build_server(spec, port=port)

    def stop(*_):
        bank.dump_to_file(a.bank_dump_path)
        write(
            ledger.root / "server_shutdown.json", dict(clean=True, final_snapshot="final.sqlite")
        )
        sys.exit(0)

    signal.signal(signal.SIGTERM, stop)
    print(f"PORT={port}", flush=True)
    try:
        mcp.run(transport="streamable-http")
    finally:
        bank.dump_to_file(a.bank_dump_path)


if __name__ == "__main__":
    main()
