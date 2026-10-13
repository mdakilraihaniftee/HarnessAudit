from __future__ import annotations

import subprocess

import pytest

from multi_agent.attacks.agent import CodexCliBackend


def test_codex_cli_timeout_is_explicit(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", timeout)
    backend = CodexCliBackend(timeout=1, retries=0)
    with pytest.raises(RuntimeError, match="timed out"):
        backend.generate("return structured output")
