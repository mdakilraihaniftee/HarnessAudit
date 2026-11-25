"""Loopback-only streaming model gateway with a durable, shared request ceiling."""

from __future__ import annotations

import contextlib
import http.client
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from .experiment import read_json, save


class RequestBudget:
    def __init__(self, path, maximum, per_trial):
        self.path, self.maximum, self.per_trial = path, maximum, per_trial
        self.lock = threading.Lock()
        self.state = (
            read_json(path)
            if path.exists()
            else {
                "maximum": maximum,
                "per_trial_maximum": per_trial,
                "used": 0,
                "by_trial": {},
                "requests": [],
            }
        )
        if self.state["maximum"] != maximum or self.state["per_trial_maximum"] != per_trial:
            raise ValueError("Model request budget differs from the persistent experiment")
        save(path, self.state)

    def reserve(self, trial):
        with self.lock:
            used = self.state["by_trial"].get(trial, 0)
            if self.state["used"] >= self.maximum or used >= self.per_trial:
                return None
            self.state["used"] += 1
            self.state["by_trial"][trial] = used + 1
            row = {
                "id": self.state["used"],
                "trial": trial,
                "started": time.time(),
                "status": "reserved",
            }
            self.state["requests"].append(row)
            save(
                self.path, self.state
            )  # Reserve before forwarding; failures still consume budget.
            return row

    def finish(self, row, status):
        with self.lock:
            row.update(status=status, finished=time.time())
            save(self.path, self.state)


@contextlib.contextmanager
def model_gateway(endpoint, port, budget):
    upstream = urlsplit(endpoint)
    if upstream.scheme != "http" or upstream.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Model gateway upstream must be a local HTTP endpoint")
    if upstream.username or upstream.password or upstream.query:
        raise ValueError("Invalid upstream endpoint")
    forbidden_headers = {
        "host",
        "authorization",
        "connection",
        "transfer-encoding",
        "keep-alive",
        "proxy-authorization",
        "content-length",
    }

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass  # Do not log request bodies, credentials or financial content.

        def respond_error(self, status, message):
            data = json.dumps(
                {"error": {"message": message, "type": "bach_budget_or_proxy"}}
            ).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self.forward(False)

        def do_POST(self):
            self.forward(True)

        def forward(self, post):
            parts = urlsplit(self.path)
            path, trial = parts.path, "preflight"
            if path.startswith("/slot/"):
                _, _, trial, suffix = path.split("/", 3)
                path = "/" + suffix
            if parts.query or path not in ({"/v1/chat/completions"} if post else {"/v1/models"}):
                self.respond_error(
                    403, "Only local model metadata and chat completions are allowed"
                )
                return
            row = budget.reserve(trial) if post else None
            if post and row is None:
                self.respond_error(429, "Persistent BACH model-request budget exhausted")
                return
            connection = http.client.HTTPConnection(
                upstream.hostname, upstream.port or 80, timeout=650
            )
            status = "transport_failure"
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length > 16 * 1024 * 1024:
                    self.respond_error(413, "Request too large")
                    return
                body = self.rfile.read(length) if length else None
                headers = {
                    k: v for k, v in self.headers.items() if k.lower() not in forbidden_headers
                }
                headers["Authorization"] = "Bearer EMPTY"
                connection.request("POST" if post else "GET", path, body, headers)
                response = connection.getresponse()
                status = response.status
                if 300 <= status < 400:
                    self.respond_error(502, "Upstream redirects are prohibited")
                    return
                self.send_response(response.status)
                for key, value in response.getheaders():
                    if key.lower() not in forbidden_headers:
                        self.send_header(key, value)
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                while chunk := response.read1(65536):
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except (OSError, ValueError, http.client.HTTPException):
                # No fallback, redirects or external proxy environment variables.
                self.close_connection = True
            finally:
                connection.close()
                if row:
                    budget.finish(row, status)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
