#!/usr/bin/env python3
"""Analyse anonymized CHVD human evaluation in long format.

Expected score file columns:
participant_id,pair_id,condition,metric,score

The script calculates matched M3-minus-M1 summaries by metric. It is an
exploratory transparent summary; add a mixed-effects model only after the final
sample and analysis plan are frozen.
"""
from __future__ import annotations

import argparse
from pathlib import Path

# Allow direct execution from a cloned repository without requiring PYTHONPATH.
import sys
_CODE_ROOT = Path(__file__).resolve().parents[1]
if str(_CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(_CODE_ROOT))

import numpy as np
import pandas as pd
from scipy.stats import wilcoxon

from chvd_tools.human_eval_utils import validate_long_human_scores


def safe_wilcoxon(values: pd.Series) -> tuple[float | None, float | None]:
    values = values.dropna()
    if len(values) == 0 or np.allclose(values.to_numpy(dtype=float), 0):
        return None, None
    result = wilcoxon(values, zero_method="wilcox", alternative="two-sided", method="auto")
    return float(result.statistic), float(result.pvalue)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Anonymized long-format score CSV.")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--condition-a", default="M1")
    parser.add_argument("--condition-b", default="M3")
    args = parser.parse_args()

    frame = pd.read_csv(args.input)
    errors = validate_long_human_scores(frame, (args.condition_a, args.condition_b))
    if errors:
        raise SystemExit("Invalid human evaluation inpu[LOCAL_PATH_REDACTED]" + "\n- ".join(errors))

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paired = frame.pivot(
        index=["participant_id", "pair_id", "metric"],
        columns="condition",
        values="score",
    ).reset_index()
    missing = {args.condition_a, args.condition_b}.difference(paired.columns)
    if missing:
        raise SystemExit(f"No complete matched rows for conditions: {sorted(missing)}")

    paired["difference_b_minus_a"] = paired[args.condition_b] - paired[args.condition_a]
    summaries = []
    for metric, subset in paired.groupby("metric", sort=True):
        diffs = subset["difference_b_minus_a"]
        statistic, pvalue = safe_wilcoxon(diffs)
        summaries.append(
            {
                "metric": metric,
                "n_matched_ratings": int(len(subset)),
                "mean_M1": float(subset[args.condition_a].mean()),
                "mean_M3": float(subset[args.condition_b].mean()),
                "mean_difference_M3_minus_M1": float(diffs.mean()),
                "median_difference_M3_minus_M1": float(diffs.median()),
                "wilcoxon_statistic": statistic,
                "wilcoxon_pvalue_unadjusted": pvalue,
            }
        )

    paired.to_csv(out_dir / "human_scores_paired_long.csv", index=False)
    pd.DataFrame(summaries).to_csv(out_dir / "human_metric_summary.csv", index=False)
    print(f"Wrote analysis for {len(summaries)} metric(s) to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
