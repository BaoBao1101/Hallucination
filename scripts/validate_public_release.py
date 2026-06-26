#!/usr/bin/env python3
"""Validate included public CHVD judge outputs without mutating frozen artifacts.

Runtime audits are written outside the immutable release_audit/ tree by default.
That preserves manifest integrity regardless of validation order.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

MAIN = Path("Study_Gemini3.1Flash-Lite") / "P1"

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument(
        "--out-dir",
        default=None,
        help="Runtime audit directory. Defaults to <root>/results/runtime_validation.",
    )
    args = parser.parse_args()
    root = Path(args.root).resolve()
    out_dir = Path(args.out_dir).resolve() if args.out_dir else root / "results" / "runtime_validation"
    main_study = root / MAIN
    validators = root / "code" / "validators"

    jobs = [
        ("validate_individual_scores.py", main_study / "Output_Individual_Scores/gpt5.5plus_score_individual.json"),
        ("validate_individual_scores.py", main_study / "Output_Individual_Scores/gemini3.1pro_score_individual.json"),
        ("validate_directional_traceability_scores.py", main_study / "Output_Paired_Wised_Score_Directional/gpt5.5plus_score_directional.json"),
        ("validate_directional_traceability_scores.py", main_study / "Output_Paired_Wised_Score_Directional/gemini3.1pro_score_directional.json"),
        (
            "validate_neutral_pairwise_scores.py",
            main_study
            / "Output_Paired_Wised_Score_Neutral/Included/ChatGPT5.5Plus_v1/gpt5.5plus_score_neutral.v1.json",
        ),
    ]

    failures = 0
    out_dir.mkdir(parents=True, exist_ok=True)
    for script, input_path in jobs:
        output_path = out_dir / f"{input_path.stem}.audit.json"
        if not input_path.exists() or input_path.stat().st_size == 0:
            print(f"FAIL missing/empty: {input_path}")
            failures += 1
            continue
        cmd = [
            sys.executable,
            str(validators / script),
            "--input",
            str(input_path),
            "--out",
            str(output_path),
        ]
        print("+", " ".join(cmd))
        failures += subprocess.run(cmd, cwd=root, check=False).returncode != 0

    bad = list((main_study / "Output_Paired_Wised_Score_Neutral/Included").glob("Gemini*"))
    if bad:
        print(f"FAIL: Gemini neutral must not be Included: {bad}")
        failures += 1

    return 1 if failures else 0

if __name__ == "__main__":
    raise SystemExit(main())
