"""CPU-only capture of the tool declarations Qwen Code actually sends to the model.

Starts a loopback stub of the OpenAI chat endpoint that records request bodies and
answers with a fixed short text, then runs the pinned Qwen Code CLI once with only
the ClawTeam MCP server attached. No model, GPU or network service is used. The
stub reply ends the session immediately; no tool is executed.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .target import REPO


def _stub(record):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def _send(self, status, body, ctype="application/json"):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True

        def do_GET(self):
            self._send(200, json.dumps({"object": "list", "data": []}).encode())

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            record.append(body)
            base = {"id": "stub", "created": int(time.time()), "model": body.get("model")}
            if body.get("stream"):
                chunks = [
                    {"index": 0, "delta": {"role": "assistant", "content": "Done."}},
                    {"index": 0, "delta": {}, "finish_reason": "stop"},
                ]
                out = b"".join(
                    b"data: "
                    + json.dumps(dict(base, object="chat.completion.chunk", choices=[c])).encode()
                    + b"\n\n"
                    for c in chunks
                )
                usage = dict(prompt_tokens=1, completion_tokens=1, total_tokens=2)
                out += (
                    b"data: "
                    + json.dumps(
                        dict(base, object="chat.completion.chunk", choices=[], usage=usage)
                    ).encode()
                    + b"\n\ndata: [DONE]\n\n"
                )
                self._send(200, out, "text/event-stream")
            else:
                message = {"role": "assistant", "content": "Done."}
                choice = {"index": 0, "message": message, "finish_reason": "stop"}
                self._send(
                    200,
                    json.dumps(dict(base, object="chat.completion", choices=[choice])).encode(),
                )

    return Handler


def capture(clawteam_entry: dict, *, qwen_bin: str, settings: dict, timeout=120):
    """Return the ``tools`` array from Qwen Code's first chat request."""
    record: list[dict] = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _stub(record))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="ha-schema-capture-") as tmp:
            tmp = Path(tmp)
            (tmp / "home").mkdir()
            (tmp / "ws").mkdir()
            (tmp / "data").mkdir()
            (tmp / "home/settings.json").write_text(json.dumps(settings))
            entry = json.loads(json.dumps(clawteam_entry))
            entry.setdefault("env", {})["CLAWTEAM_DATA_DIR"] = str(tmp / "data")
            (tmp / "mcp.json").write_text(json.dumps({"mcpServers": {"clawteam": entry}}))
            url = f"http://127.0.0.1:{server.server_port}/v1"
            env = {
                k: v
                for k, v in os.environ.items()
                if not k.startswith(("OPENAI_", "ANTHROPIC_", "HF_TOKEN", "OPENROUTER"))
            }
            env.update(
                QWEN_HOME=str(tmp / "home"),
                QWEN_CODE_SYSTEM_SETTINGS_PATH=str(tmp / "home/settings.json"),
                QWEN_USAGE_STATISTICS_ENABLED="0",
                QWEN_CODE_SUPPRESS_YOLO_WARNING="1",
                QWEN_CODE_DISABLE_LSP="1",
                OPENAI_API_KEY="EMPTY",
                OPENAI_BASE_URL=url,
                OPENAI_MODEL="Qwen/Qwen3-8B",
            )
            proc = subprocess.run(
                [
                    qwen_bin,
                    "--bare",
                    "--auth-type",
                    "openai",
                    "--output-format",
                    "stream-json",
                    "--advisor",
                    "off",
                    "--max-session-turns",
                    "2",
                    "--exclude-tools",
                    "web_search",
                    "--exclude-tools",
                    "web_fetch",
                    "--openai-base-url",
                    url,
                    "--model",
                    "Qwen/Qwen3-8B",
                    "--mcp-config",
                    str(tmp / "mcp.json"),
                    "--yolo",
                    "-p",
                    "Reply with the word Done.",
                ],
                cwd=tmp / "ws",
                env=env,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            init = next(
                (
                    json.loads(line)
                    for line in proc.stdout.splitlines()
                    if line.startswith("{") and '"subtype":"init"' in line.replace(" ", "")
                ),
                {},
            )
    finally:
        server.shutdown()
        server.server_close()
    if not record:
        raise RuntimeError(f"Qwen Code sent no model request (exit {proc.returncode})")
    return dict(
        tools=record[0].get("tools", []),
        request_keys=sorted(record[0]),
        mcp_servers=init.get("mcp_servers"),
        qwen_exit_code=proc.returncode,
    )


def model_visible_comparison(qwen_bin: str, settings: dict) -> dict:
    """Capture original and TARGET_V1R4 ClawTeam declarations as Qwen Code sends them."""
    from .tool_contract import server_config

    original = {
        "command": str(REPO / ".venv/bin/clawteam-mcp"),
        "args": [],
        "trust": True,
        "timeout": 120000,
    }
    out = {}
    for label, entry in [("original", original), ("target_v1r4", server_config(original))]:
        captured = capture(entry, qwen_bin=qwen_bin, settings=settings)
        out[label] = dict(
            captured,
            clawteam_tools={
                t["function"]["name"]: t["function"]
                for t in captured["tools"]
                if t.get("function", {}).get("name", "").startswith("mcp__clawteam__")
            },
        )
    return out
