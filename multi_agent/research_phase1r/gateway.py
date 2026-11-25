"""Local request budget, explicit trial seeds and observable error responses."""

from __future__ import annotations

import contextlib
import hashlib
import http.client
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from multi_agent.research_phase1.request_proxy import RequestBudget

from .audit import write


def trial_seed(master, trial_id):
    return (
        int.from_bytes(hashlib.sha256(f"{master}:{trial_id}".encode()).digest()[:4], "big")
        & 0x7FFFFFFF
    )


@contextlib.contextmanager
def gateway(endpoint, port, budget: RequestBudget, seeds):
    upstream = urlsplit(endpoint)
    if (
        upstream.scheme != "http"
        or upstream.hostname not in {"127.0.0.1", "localhost", "::1"}
        or upstream.username
        or upstream.query
    ):
        raise ValueError("Only local plain HTTP upstream is permitted")
    excluded = {
        "host",
        "authorization",
        "connection",
        "content-length",
        "transfer-encoding",
        "proxy-authorization",
        "keep-alive",
    }

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def error(self, status, text):
            data = json.dumps({"error": {"message": text}}).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self.forward(False)

        def do_POST(self):
            self.forward(True)

        def forward(self, post):
            parts = urlsplit(self.path)
            path = parts.path
            slot = None
            if path.startswith("/slot/"):
                bits = path.split("/", 3)
                if len(bits) != 4:
                    self.error(403, "Malformed route")
                    return
                slot, path = bits[2], "/" + bits[3]
            if (
                parts.query
                or path not in ({"/v1/chat/completions"} if post else {"/v1/models"})
                or (post and slot not in seeds)
            ):
                self.error(403, "Unknown diagnostic route")
                return
            row = budget.reserve(slot) if post else None
            if post and row is None:
                with budget.lock:
                    budget.state.setdefault("censored_trials", []).append(slot)
                    write(budget.path, budget.state)
                self.error(429, "Persistent request budget exhausted")
                return
            conn = http.client.HTTPConnection(upstream.hostname, upstream.port or 80, timeout=650)
            status = "transport_failure"
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 0 or length > 16 * 1024 * 1024:
                    status = 413
                    self.error(413, "Invalid request length")
                    return
                data = self.rfile.read(length) if length else None
                if post:
                    body = json.loads(data)
                    body["seed"] = seeds[slot]
                    data = json.dumps(body).encode()
                    with budget.lock:
                        row.update(
                            model=body.get("model"),
                            trial_seed=seeds[slot],
                            seed_in_outgoing_payload=True,
                            request_seed_policy="same derived trial seed on each request",
                            temperature=body.get("temperature"),
                            max_tokens=body.get("max_tokens"),
                            message_count=len(body.get("messages", [])),
                            payload_bytes=len(data),
                            thinking=(body.get("chat_template_kwargs") or {}).get(
                                "enable_thinking"
                            ),
                            backend_honors_seed=(
                                "vLLM supports request seed; empirical determinism not established"
                            ),
                        )
                headers = {k: v for k, v in self.headers.items() if k.lower() not in excluded}
                headers["Authorization"] = "Bearer EMPTY"
                conn.request("POST" if post else "GET", path, data, headers)
                resp = conn.getresponse()
                status = resp.status
                if 300 <= status < 400:
                    self.error(502, "Redirect prohibited")
                    return
                self.send_response(status)
                for k, v in resp.getheaders():
                    if k.lower() not in excluded:
                        self.send_header(k, v)
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                error_body = bytearray()
                while chunk := resp.read1(65536):
                    if status >= 400 and len(error_body) < 16384:
                        error_body.extend(chunk[: 16384 - len(error_body)])
                    self.wfile.write(chunk)
                    self.wfile.flush()
                if row and error_body:
                    row["error_response"] = error_body.decode(errors="replace")
            except (OSError, ValueError, http.client.HTTPException) as exc:
                if row:
                    row["gateway_error"] = f"{type(exc).__name__}: {exc}"
                self.close_connection = True
            finally:
                conn.close()
                if row:
                    budget.finish(row, status)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
