#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow direct execution from a cloned repository without requiring PYTHONPATH.
import sys
_CODE_ROOT = Path(__file__).resolve().parents[1]
if str(_CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(_CODE_ROOT))

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from chvd_tools.io_utils import load_json, write_json
from chvd_tools.validation import validate_neutral_pairwise_payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate CHVD score-first neutral pairwise output JSON.")
    parser.add_argument("--input", required=True, help="Path to neutral pairwise score JSON.")
    parser.add_argument("--out", required=True, help="Path for audit JSON.")
    parser.add_argument("--tie-margin", type=float, default=0.1, help="Absolute score gap treated as TIE. Default 0.1.")
    parser.add_argument("--skip-preference-consistency", action="store_true")
    parser.add_argument("--allow-invalid", action="store_true")
    args = parser.parse_args()

    payload = load_json(Path(args.input).expanduser().resolve())
    audit = validate_neutral_pairwise_payload(
        payload,
        strict_preference_consistency=not args.skip_preference_consistency,
        tie_margin=args.tie_margin,
    )
    write_json(Path(args.out).expanduser().resolve(), audit)

    print(f"schema={audit['schema']}")
    print(f"records={audit['n_records']}")
    print(f"unique_ids={audit.get('unique_ids', 0)}")
    print(f"issues={len(audit['issues'])}")
    if not audit["valid"] and not args.allow_invalid:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
