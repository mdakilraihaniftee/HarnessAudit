"""Run one HarnessAudit task from Python."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TASK = REPO_ROOT / "multi_agent/tasks/daily_life/wellness/dl-t4.yaml"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", type=Path, default=DEFAULT_TASK)
    parser.add_argument("--framework", default="clawteam")
    parser.add_argument("--harness", default="openclaw")
    parser.add_argument("--model", default="qwen3.5-plus")
    parser.add_argument("--judge-model", default="gpt-5.4")
    parser.add_argument("--judge-workers", type=int, default=4)
    parser.add_argument("--trace-dir", type=Path, default=REPO_ROOT / "multi_agent/traces")
    parser.add_argument("--output", type=Path, default=REPO_ROOT / "multi_agent/results")
    parser.add_argument(
        "--skip-judge",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Skip LLM-as-judge calls; useful for no-key environment checks.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    task = args.task if args.task.is_absolute() else REPO_ROOT / args.task
    command = [
        sys.executable,
        "-m",
        "multi_agent",
        "run",
        str(task),
        "--framework",
        args.framework,
        "--harness",
        args.harness,
        "--model",
        args.model,
        "--judge-model",
        args.judge_model,
        "--judge-workers",
        str(args.judge_workers),
        "--trace-dir",
        str(args.trace_dir),
        "--output",
        str(args.output),
    ]
    if args.skip_judge:
        command.append("--skip-judge")

    print("Running:", " ".join(command))
    completed = subprocess.run(command, cwd=REPO_ROOT, check=False)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())