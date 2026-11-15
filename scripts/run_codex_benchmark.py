#!/usr/bin/env python3
"""Run one ClawTeam benchmark task (default dl-t4) with the Codex harness.

Uses the Codex CLI on PATH (on RIT: installed by rc/setup.sh) and the Python that runs
this script. Credentials come from the environment (a repo-root .env is optional).
Usage: python scripts/run_codex_benchmark.py [TASK_YAML] [--model ...] [--judge-model ...]
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "task",
        nargs="?",
        default="multi_agent/tasks/daily_life/wellness/dl-t4.yaml",
    )
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument("--judge-model", default="gpt-5.6-sol")
    parser.add_argument("--judge-workers", type=int, default=4)
    parser.add_argument("--trace-dir", default="multi_agent/traces")
    parser.add_argument("--output", default="multi_agent/results")
    args = parser.parse_args()

    if not shutil.which("codex"):
        print(
            "codex is not on PATH. On RIT it is installed by rc/setup.sh; "
            "locally: npm install -g @openai/codex",
            file=sys.stderr,
        )
        return 1

    command = [
        sys.executable,
        "-m",
        "multi_agent",
        "run",
        args.task,
        "--framework",
        "clawteam",
        "--harness",
        "codex",
        "--model",
        args.model,
        "--judge-model",
        args.judge_model,
        "--judge-workers",
        str(args.judge_workers),
        "--trace-dir",
        args.trace_dir,
        "--output",
        args.output,
    ]
    try:
        completed = subprocess.run(command, cwd=REPO_ROOT, env=os.environ.copy(), check=False)
    except KeyboardInterrupt:
        print("Benchmark interrupted.", file=sys.stderr)
        return 130
    if completed.returncode:
        print(f"Benchmark failed with exit code {completed.returncode}.", file=sys.stderr)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
