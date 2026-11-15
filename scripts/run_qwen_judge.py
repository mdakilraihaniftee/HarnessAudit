#!/usr/bin/env python3
"""Run one multi-agent task with Codex agents and an OpenRouter-hosted Qwen judge.

Agents use the Codex CLI on PATH (on RIT: installed by rc/setup.sh) with their usual
OpenAI credentials (CODEX_AUTH_MODE=api_key -> OPENAI_API_KEY). Only the LLM judge is
routed through OpenRouter (OPENROUTER_API_KEY, optional OPENROUTER_BASE_URL) via
MASP_JUDGE_PROVIDER=openrouter. A repo-root .env is loaded if present.

Usage: python scripts/run_qwen_judge.py [TASK_YAML] [--agent-model ...] [--judge-model ...]
For the full sweep with this setup see the alternative FULL command in rc/project.env.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def configure_openrouter_judge() -> None:
    """Route only the JSON judge through OpenRouter (OPENAI_* is left for the agents)."""

    load_dotenv(REPO_ROOT / ".env")  # optional; no-op when the file does not exist
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    base_url = os.environ.get("OPENROUTER_BASE_URL", "").strip()
    if not api_key or api_key == "...":
        raise SystemExit("OPENROUTER_API_KEY is missing or still a placeholder")
    if base_url and not base_url.startswith(("http://", "https://")):
        raise SystemExit("OPENROUTER_BASE_URL must start with http:// or https://")
    os.environ["MASP_JUDGE_PROVIDER"] = "openrouter"


def require_codex_on_path() -> None:
    if not shutil.which("codex"):
        raise SystemExit(
            "codex is not on PATH. On RIT it is installed by rc/setup.sh; "
            "locally: npm install -g @openai/codex"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "task",
        nargs="?",
        default="multi_agent/tasks/daily_life/wellness/dl-t4.yaml",
    )
    parser.add_argument("--agent-model", default="gpt-5.6-sol")
    parser.add_argument("--judge-model", default="qwen/qwen3-8b")
    parser.add_argument("--judge-workers", type=int, default=4)
    parser.add_argument("--agent-timeout", type=int, default=300)
    parser.add_argument("--trace-dir", default="multi_agent/traces")
    parser.add_argument("--output", default="multi_agent/results")
    args = parser.parse_args()

    configure_openrouter_judge()
    require_codex_on_path()
    os.chdir(REPO_ROOT)
    sys.argv = [
        "multi_agent",
        "run",
        args.task,
        "--framework",
        "clawteam",
        "--harness",
        "codex",
        "--model",
        args.agent_model,
        "--judge-model",
        args.judge_model,
        "--judge-workers",
        str(args.judge_workers),
        "--agent-timeout",
        str(args.agent_timeout),
        "--trace-dir",
        args.trace_dir,
        "--output",
        args.output,
    ]

    from multi_agent.cli import main as multi_agent_main

    multi_agent_main()


if __name__ == "__main__":
    main()
