#!/usr/bin/env python3
"""Validate anonymized CHVD human-evaluation long-format data."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

# Allow direct execution from a cloned repository without requiring PYTHONPATH.
import sys
_CODE_ROOT = Path(__file__).resolve().parents[1]
if str(_CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(_CODE_ROOT))

import pandas as pd

from chvd_tools.human_eval_utils import validate_long_human_scores


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--condition-a", default="M1")
    parser.add_argument("--condition-b", default="M3")
    args = parser.parse_args()

    frame = pd.read_csv(args.input)
    errors = validate_long_human_scores(frame, (args.condition_a, args.condition_b))
    payload = {
        "schema": "chvd_human_evaluation_long_v1",
        "records": int(len(frame)),
        "issues": errors,
        "valid": not errors,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
