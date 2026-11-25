#!/usr/bin/env python3
"""Standalone CPU entry point; run from repository root or installed checkout."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from multi_agent.research_phase1r.audit import reconstruct

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--pilot-dir", default="results/research_phase1/pilot_15_real")
    p.add_argument("--output-dir", default="results/research_phase1r/original_pilot_audit")
    a = p.parse_args()
    raise SystemExit(0 if reconstruct(a.pilot_dir, a.output_dir) else 2)
