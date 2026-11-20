"""Local-Qwen attacker backends for SI-ATG.

``QwenCodeAttackerBackend`` (``backend: qwen_code``)
    Headless Qwen Code CLI, the local counterpart of ``CodexCliBackend``: an
    ephemeral, isolated ``QWEN_HOME`` and temp workspace per call, ``--bare``
    mode, ``--json-schema`` structured output and ``--max-tool-calls 0`` (no
    tool except the synthetic ``structured_output`` may run).

``LocalOpenAIAttackerBackend`` (``backend: local_openai``)
    Direct chat-completions call with server-side JSON-schema decoding.

Both only ever talk to the resolved ``attacker_generator`` route; in local_only
mode that route must be loopback or explicitly approved, and nothing falls back
to a hosted provider. They only *generate* the requester note — the learning
loop (memory, strategy selection, reflection, harness optimization) stays in
``AttackerAgent`` unchanged.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

GENERATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "request_text", "attack_hypothesis",
        "intended_target_roles", "expected_violation_types",
    ],
    "properties": {
        "request_text": {"type": "string"},
        "attack_hypothesis": {"type": "string"},
        "intended_target_roles": {"type": "array", "items": {"type": "string"}},
        "expected_violation_types": {
            "type": "array",
            "items": {"type": "string", "enum": ["V-OT", "V-OR", "V-IC", "V-ID"]},
        },
    },
    "additionalProperties": False,
}


def _route():
    from multi_agent.local_inference.router import (
        is_local_only,
        require_local_route,
        resolve_route,
    )

    return (require_local_route if is_local_only() else resolve_route)("attacker_generator")


class QwenCodeAttackerBackend:
    """Generate attack candidates with the Qwen Code CLI against a local model."""

    def __init__(self, timeout: int | None = None, retries: int | None = None,
                 executable: str | None = None) -> None:
        from multi_agent.frameworks.native.qwen_code.runtime import resolve_qwen_bin
        from multi_agent.local_inference.config import current_config

        cfg = current_config().harness.qwen_code
        self.route = _route()
        self.timeout = timeout or cfg.attacker_timeout_s
        self.retries = cfg.attacker_retries if retries is None else retries
        self.executable = resolve_qwen_bin(executable or cfg.bin)
        self.calls: list[dict[str, Any]] = []

    def _command(self, schema_path: Path) -> list[str]:
        return [
            self.executable, "--bare", "--auth-type", "openai",
            "--openai-base-url", self.route.endpoint, "--model", self.route.model,
            "--output-format", "json", "--json-schema", f"@{schema_path}",
            "--max-tool-calls", "0", "--max-session-turns", "4",
            "--max-wall-time", f"{max(30, self.timeout - 10)}s", "--advisor", "off",
            "--chat-recording", "false",
        ]

    def _env(self, home: Path) -> dict[str, str]:
        from multi_agent.local_inference.router import scrubbed_child_env

        env = scrubbed_child_env(dict(os.environ))
        env.update({
            "QWEN_HOME": str(home),
            "QWEN_CODE_SYSTEM_SETTINGS_PATH": str(home / "settings.json"),
            "QWEN_USAGE_STATISTICS_ENABLED": "0",
            "QWEN_CODE_SUPPRESS_YOLO_WARNING": "1",
            "OPENAI_API_KEY": self.route.api_key,
            "OPENAI_BASE_URL": self.route.endpoint,
            "OPENAI_MODEL": self.route.model,
        })
        return env

    @staticmethod
    def _settings(route: Any) -> dict[str, Any]:
        return {
            "security": {"auth": {"selectedType": "openai"}},
            "privacy": {"usageStatisticsEnabled": False},
            "telemetry": {"enabled": False},
            "general": {"enableAutoUpdate": False},
            "memory": {"enableManagedAutoMemory": False, "enableManagedAutoDream": False,
                       "enableAutoSkill": False},
            "model": {
                "name": route.model, "catalogRefresh": False,
                "generationConfig": {
                    "samplingParams": {"temperature": route.temperature,
                                       "max_tokens": route.max_output_tokens},
                    "extra_body": {"chat_template_kwargs": {
                        "enable_thinking": route.reasoning_mode == "on"}},
                },
            },
        }

    @staticmethod
    def _extract(stdout: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        try:
            events = json.loads(stdout)
        except json.JSONDecodeError:
            events = [json.loads(line) for line in stdout.splitlines()
                      if line.strip().startswith("{")]
        if isinstance(events, dict):
            events = [events]
        meta: dict[str, Any] = {}
        structured = None
        for event in events:
            if not isinstance(event, dict):
                continue
            if event.get("type") == "assistant":
                for block in (event.get("message") or {}).get("content") or []:
                    if (block.get("type") == "tool_use"
                            and block.get("name") == "structured_output"):
                        structured = block.get("input")
            if event.get("type") == "result":
                meta = {k: event.get(k) for k in ("subtype", "is_error", "usage",
                                                  "duration_ms", "num_turns")}
                if structured is None and isinstance(event.get("result"), str):
                    try:
                        structured = json.loads(event["result"])
                    except json.JSONDecodeError:
                        pass
        return (structured if isinstance(structured, dict) else None), meta

    def generate(self, prompt: str) -> dict[str, Any]:
        from multi_agent.local_inference.performance import record_request

        from .agent import _validate_generation

        last_error = ""
        for attempt in range(self.retries + 1):
            with tempfile.TemporaryDirectory(prefix="harnessaudit-qwen-attacker-") as temp:
                root = Path(temp)
                home, work = root / "qwen_home", root / "work"
                home.mkdir()
                work.mkdir()
                (home / "settings.json").write_text(json.dumps(self._settings(self.route)))
                schema_path = root / "schema.json"
                schema_path.write_text(json.dumps(GENERATION_SCHEMA))
                started = time.perf_counter()
                try:
                    done = subprocess.run(
                        self._command(schema_path), input=prompt, text=True,
                        capture_output=True, timeout=self.timeout, cwd=work,
                        env=self._env(home), check=False,
                    )
                except subprocess.TimeoutExpired:
                    last_error = f"Qwen Code attacker timed out after {self.timeout}s"
                    self.calls.append({"attempt": attempt, "error": last_error})
                    continue
                latency = time.perf_counter() - started
                data, meta = self._extract(done.stdout)
                usage = meta.get("usage") or {}
                record_request("attacker_generator", self.route.model,
                               {"prompt_tokens": usage.get("input_tokens"),
                                "completion_tokens": usage.get("output_tokens")},
                               finish_reason=meta.get("subtype"), latency_s=latency)
                self.calls.append({"attempt": attempt, "returncode": done.returncode,
                                   "latency_s": round(latency, 3), **meta})
                if done.returncode == 0 and data is not None:
                    try:
                        _validate_generation(data)
                        return data
                    except ValueError as exc:
                        last_error = f"invalid structured output: {exc}"
                else:
                    detail = (done.stderr or done.stdout).strip()[-800:]
                    last_error = f"Qwen Code exited {done.returncode}: {detail}"
                    if "ECONNREFUSED" in detail or "fetch failed" in detail:
                        raise RuntimeError(
                            f"Local attacker endpoint {self.route.endpoint} is unreachable; "
                            "no hosted fallback is attempted. Start the local server."
                        )
            if attempt < self.retries:
                time.sleep(min(2 ** attempt, 4))
        raise RuntimeError(last_error or "Qwen Code attacker failed to generate a candidate")


class LocalOpenAIAttackerBackend:
    """Generate candidates with server-side JSON-schema decoding (no CLI)."""

    def __init__(self, timeout: float = 300.0, retries: int = 2) -> None:
        self.route = _route()
        self.timeout = timeout
        self.retries = retries

    def generate(self, prompt: str) -> dict[str, Any]:
        from openai import OpenAI

        from multi_agent.local_inference.performance import record_request

        from .agent import _validate_generation

        client = OpenAI(base_url=self.route.endpoint, api_key=self.route.api_key,
                        max_retries=0, timeout=self.timeout)
        last_error = ""
        try:
            for attempt in range(self.retries + 1):
                started = time.perf_counter()
                try:
                    resp = client.chat.completions.create(
                        model=self.route.model,
                        messages=[
                            {"role": "system", "content": "Return only the requested JSON."},
                            {"role": "user", "content": prompt},
                        ],
                        temperature=self.route.temperature,
                        max_tokens=self.route.max_output_tokens,
                        response_format={"type": "json_schema", "json_schema": {
                            "name": "attack_candidate", "schema": GENERATION_SCHEMA,
                            "strict": True}},
                        extra_body={"chat_template_kwargs": {
                            "enable_thinking": self.route.reasoning_mode == "on"}},
                    )
                except Exception as exc:  # noqa: BLE001
                    name = type(exc).__name__
                    if "Connection" in name or "Timeout" in name:
                        raise RuntimeError(
                            f"Local attacker endpoint {self.route.endpoint} unavailable "
                            f"({name}); no hosted fallback is attempted."
                        ) from exc
                    last_error = f"{name}: {exc}"
                    continue
                record_request("attacker_generator", self.route.model, resp.usage,
                               finish_reason=resp.choices[0].finish_reason,
                               latency_s=time.perf_counter() - started)
                try:
                    data = json.loads(resp.choices[0].message.content or "")
                    _validate_generation(data)
                    return data
                except (json.JSONDecodeError, ValueError) as exc:
                    last_error = f"invalid structured output: {exc}"
                if attempt < self.retries:
                    time.sleep(min(2 ** attempt, 4))
        finally:
            client.close()
        raise RuntimeError(last_error or "local attacker generation failed")


def qwen_code_available() -> bool:
    try:
        from multi_agent.frameworks.native.qwen_code.runtime import resolve_qwen_bin

        return bool(resolve_qwen_bin())
    except RuntimeError:
        return bool(shutil.which("qwen"))
