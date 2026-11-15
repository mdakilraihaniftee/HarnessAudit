#!/usr/bin/env python3
"""Verify that the configured OpenRouter Qwen judge returns valid JSON.

Credentials come from the environment (OPENROUTER_API_KEY, optional OPENROUTER_BASE_URL);
a repo-root .env is loaded if present. Usage: python scripts/probe_qwen_judge.py
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def configure_openrouter_judge() -> None:
    """Route only the JSON judge through OpenRouter (OPENAI_* is left untouched)."""

    load_dotenv(REPO_ROOT / ".env")  # optional; no-op when the file does not exist
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    base_url = os.environ.get("OPENROUTER_BASE_URL", "").strip()
    if not api_key or api_key == "...":
        raise SystemExit("OPENROUTER_API_KEY is missing or still a placeholder")
    if base_url and not base_url.startswith(("http://", "https://")):
        raise SystemExit("OPENROUTER_BASE_URL must start with http:// or https://")
    os.environ["MASP_JUDGE_PROVIDER"] = "openrouter"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default="qwen/qwen3-8b")
    args = parser.parse_args()
    configure_openrouter_judge()

    from multi_agent.openai_json import create_json_response

    result = create_json_response(
        model=args.model,
        system_prompt="Return only valid JSON.",
        user_prompt="Return an object with key ok and boolean value true.",
        max_output_tokens=64,
    )
    if result != {"ok": True}:
        raise SystemExit(f"Unexpected judge response: {result!r}")
    print(f"Judge probe passed: {args.model} returned valid JSON")


if __name__ == "__main__":
    main()
