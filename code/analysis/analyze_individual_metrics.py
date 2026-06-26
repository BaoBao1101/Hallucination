#!/usr/bin/env python3
"""
Analyze CHVD individual candidate scores.

Inputs
------
- Private blind key: candidate_id, method_true, domain, pair_key, ...
- One or more validated judge JSON outputs.

Outputs
-------
- input integrity audit;
- unblinded long candidate score table;
- method × metric summaries;
- primary matched M1→M3 paired Wilcoxon/Holm statistics;
- descriptive/exploratory unpaired M0 contrasts;
- cross-judge score concordance.

Do not pool raw scores across judge labels. The script reports judge-specific
statistics and judge-to-judge concordance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

# Allow direct execution from a cloned repository without requiring PYTHONPATH.
import sys
_CODE_ROOT = Path(__file__).resolve().parents[1]
if str(_CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(_CODE_ROOT))
from typing import Any, Dict, List

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from statsmodels.stats.multitest import multipletests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from chvd_tools.constants import INDIVIDUAL_METRICS, METHOD_M0, METHOD_M1, METHOD_M3
from chvd_tools.io_utils import load_json, parse_judge_specs, require_columns, sha256_file, write_json
from chvd_tools.stats_utils import bootstrap_mean_ci, safe_mannwhitney, safe_wilcoxon
from chvd_tools.validation import validate_individual_payload


def load_and_validate(path: Path) -> pd.DataFrame:
    payload = load_json(path)
    audit = validate_individual_payload(payload)
    if not audit["valid"]:
        raise ValueError(f"Invalid individual score file {path}: {audit['issues'][:5]}")
    return pd.DataFrame(payload["evaluations"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--key", required=True, help="Private blind key CSV.")
    parser.add_argument("--judge", action="append", required=True, help="LABEL=/path/to/score.json; repeat per judge.")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--bootstrap", type=int, default=20000)
    parser.add_argument("--seed", type=int, default=20260623)
    args = parser.parse_args()

    key_path = Path(args.key).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    judge_specs = parse_judge_specs(args.judge)

    key = pd.read_csv(key_path)
    require_columns(key, ["candidate_id", "method_true", "domain", "pair_key"], "blind key")
    if key["candidate_id"].duplicated().any():
        raise ValueError("Blind key contains duplicate candidate_id values.")

    audit_rows: List[Dict[str, Any]] = []
    long_rows: List[Dict[str, Any]] = []

    for judge, score_path in judge_specs:
        scores = load_and_validate(score_path)
        if set(scores["candidate_id"]) != set(key["candidate_id"]):
            missing = sorted(set(key["candidate_id"]) - set(scores["candidate_id"]))
            unexpected = sorted(set(scores["candidate_id"]) - set(key["candidate_id"]))
            raise ValueError(f"{judge}: key mismatch; missing={missing[:10]}, unexpected={unexpected[:10]}")

        merged = scores.merge(key, on="candidate_id", validate="one_to_one")
        rationale_norm = merged["short_rationale"].astype(str).str.lower().str.replace(r"\s+", " ", regex=True).str.strip()
        profiles = [tuple(float(row["scores"][m]) for m in INDIVIDUAL_METRICS) for _, row in merged.iterrows()]

        audit_rows.append({
            "judge": judge,
            "source_file": score_path.name,
            "sha256": sha256_file(score_path),
            "n_scored_candidates": len(merged),
            "unique_candidate_ids": merged["candidate_id"].nunique(),
            "unique_rationales": rationale_norm.nunique(),
            "most_repeated_rationale_count": int(rationale_norm.value_counts().iloc[0]),
            "unique_10_metric_profiles": len(set(profiles)),
            "most_repeated_profile_count": int(pd.Series(profiles).value_counts().iloc[0]),
        })

        for _, row in merged.iterrows():
            for metric in INDIVIDUAL_METRICS:
                long_rows.append({
                    "judge": judge,
                    "candidate_id": row["candidate_id"],
                    "prompt_regime": row.get("prompt_regime", ""),
                    "method": row["method_true"],
                    "domain": row["domain"],
                    "pair_key": row["pair_key"],
                    "metric": metric,
                    "score": float(row["scores"][metric]),
                })

    audit = pd.DataFrame(audit_rows)
    long = pd.DataFrame(long_rows)

    # Method means by judge and metric.
    summary = (
        long.groupby(["judge", "method", "metric"], as_index=False)
        .agg(n=("score", "count"), mean=("score", "mean"), sd=("score", "std"), median=("score", "median"))
    )

    # Primary matched M1→M3 paired results.
    paired_rows = []
    for judge in long["judge"].unique():
        for metric in INDIVIDUAL_METRICS:
            # M0 candidates are intentionally unmatched in this design and may have blank pair_key.
            # Restrict the primary paired estimand to real M1/M3 matched records only.
            subset = long[
                (long["judge"] == judge)
                & (long["metric"] == metric)
                & (long["method"].isin([METHOD_M1, METHOD_M3]))
                & (long["pair_key"].notna())
            ].copy()
            pivot = subset.pivot(index="pair_key", columns="method", values="score")
            if METHOD_M1 not in pivot.columns or METHOD_M3 not in pivot.columns:
                continue
            pair = pivot[[METHOD_M1, METHOD_M3]].dropna()
            delta = pair[METHOD_M3] - pair[METHOD_M1]
            w, p = safe_wilcoxon(delta)
            low, high = bootstrap_mean_ci(delta, args.bootstrap, args.seed)
            paired_rows.append({
                "judge": judge,
                "metric": metric,
                "n_pairs": len(pair),
                "M1_mean": pair[METHOD_M1].mean(),
                "M3_mean": pair[METHOD_M3].mean(),
                "mean_delta_M3_minus_M1": delta.mean(),
                "median_delta_M3_minus_M1": delta.median(),
                "bootstrap_95_low_mean_delta": low,
                "bootstrap_95_high_mean_delta": high,
                "M3_higher_count": int((delta > 0).sum()),
                "M1_higher_count": int((delta < 0).sum()),
                "exact_tie_count": int((delta == 0).sum()),
                "wilcoxon_W": w,
                "wilcoxon_p_raw": p,
            })

    paired = pd.DataFrame(paired_rows)
    if not paired.empty:
        paired["wilcoxon_p_holm"] = np.nan
        for judge in paired["judge"].unique():
            idx = paired.index[paired["judge"] == judge]
            valid = paired.loc[idx, "wilcoxon_p_raw"].notna()
            if valid.any():
                paired.loc[idx[valid], "wilcoxon_p_holm"] = multipletests(
                    paired.loc[idx[valid], "wilcoxon_p_raw"], method="holm"
                )[1]
        paired["significant_after_holm"] = paired["wilcoxon_p_holm"] < 0.05

    # Exploratory unpaired M0 contrasts only.
    contrast_rows = []
    comparisons = [(METHOD_M0, METHOD_M1), (METHOD_M0, METHOD_M3)]
    for judge in long["judge"].unique():
        for metric in INDIVIDUAL_METRICS:
            sub = long[(long["judge"] == judge) & (long["metric"] == metric)]
            for left, right in comparisons:
                a = sub.loc[sub["method"] == left, "score"]
                b = sub.loc[sub["method"] == right, "score"]
                if len(a) == 0 or len(b) == 0:
                    continue
                u, p = safe_mannwhitney(a, b)
                contrast_rows.append({
                    "judge": judge,
                    "metric": metric,
                    "comparison": f"{right}_minus_{left}",
                    "left_method": left,
                    "right_method": right,
                    "left_mean": a.mean(),
                    "right_mean": b.mean(),
                    "mean_difference_right_minus_left": b.mean() - a.mean(),
                    "mannwhitney_U": u,
                    "mannwhitney_p_raw": p,
                })

    contrasts = pd.DataFrame(contrast_rows)
    if not contrasts.empty:
        contrasts["mannwhitney_p_holm"] = np.nan
        for judge in contrasts["judge"].unique():
            idx = contrasts.index[contrasts["judge"] == judge]
            valid = contrasts.loc[idx, "mannwhitney_p_raw"].notna()
            if valid.any():
                contrasts.loc[idx[valid], "mannwhitney_p_holm"] = multipletests(
                    contrasts.loc[idx[valid], "mannwhitney_p_raw"], method="holm"
                )[1]
        contrasts["exploratory_only"] = True

    # Cross-judge candidate-level score concordance.
    concord_rows = []
    for metric in INDIVIDUAL_METRICS:
        m = long[long["metric"] == metric]
        pivot = m.pivot(index="candidate_id", columns="judge", values="score")
        judges = list(pivot.columns)
        for i in range(len(judges)):
            for j in range(i + 1, len(judges)):
                x = pivot[[judges[i], judges[j]]].dropna()
                rho, p = spearmanr(x[judges[i]], x[judges[j]])
                concord_rows.append({
                    "metric": metric,
                    "judge_a": judges[i],
                    "judge_b": judges[j],
                    "n_candidates": len(x),
                    "spearman_rho": rho,
                    "spearman_p": p,
                    "mean_absolute_difference": float(np.abs(x[judges[i]] - x[judges[j]]).mean()),
                })
    concordance = pd.DataFrame(concord_rows)

    # Save.
    pd.DataFrame([
        {"role": "private_blind_key", "file": key_path.name, "sha256": sha256_file(key_path)}
        ] + [
        {"role": f"judge_output::{label}", "file": p.name, "sha256": sha256_file(p)}
        for label, p in judge_specs
    ]).to_csv(out_dir / "00_input_manifest_sha256.csv", index=False)
    audit.to_csv(out_dir / "01_judge_output_quality_audit.csv", index=False)
    long.to_csv(out_dir / "02_unblinded_individual_scores_long.csv", index=False)
    summary.to_csv(out_dir / "03_method_metric_summary.csv", index=False)
    paired.to_csv(out_dir / "04_primary_paired_M1_to_M3_statistics.csv", index=False)
    contrasts.to_csv(out_dir / "05_exploratory_unpaired_M0_contrasts.csv", index=False)
    concordance.to_csv(out_dir / "06_cross_judge_score_concordance.csv", index=False)

    report = [
        "# CHVD Individual Metrics Analysis\n\n",
        "## Interpretation\n\n",
        "Primary inferential comparison: matched `M1_RAW_HIGH_TEMP → M3_CHVD_DISTILLED` within `pair_key`.\n\n",
        "M0 contrasts are explicitly exploratory and unpaired unless a separate matching design is documented.\n\n",
        f"Judges analyzed: {', '.join(label for label, _ in judge_specs)}.\n",
    ]
    (out_dir / "analysis_report.md").write_text("".join(report), encoding="utf-8")
    print(f"[OK] Wrote individual analysis to {out_dir}")


if __name__ == "__main__":
    main()
