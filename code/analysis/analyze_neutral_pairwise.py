#!/usr/bin/env python3
"""
Analyze score-first, neutral blinded CHVD pairwise outputs.

Input key columns:
- pair_id
- candidate_a_method
- candidate_b_method
- domain
- source_idea_id

Expected output JSON:
- pair_id
- candidate_a_scores
- candidate_b_scores
- preferences
- short_rationale

The score-file validator is run before unblinding.
"""
from __future__ import annotations

import argparse
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
from scipy.stats import spearmanr, binomtest
from sklearn.metrics import cohen_kappa_score
from statsmodels.stats.multitest import multipletests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from chvd_tools.constants import INDIVIDUAL_METRICS, METHOD_M1, METHOD_M3, PREFERENCE_MAP, RISK_METRICS
from chvd_tools.io_utils import load_json, parse_judge_specs, require_columns, sha256_file
from chvd_tools.stats_utils import bootstrap_mean_ci, safe_wilcoxon, wilson_interval
from chvd_tools.validation import validate_neutral_pairwise_payload


def load_and_validate(path: Path) -> pd.DataFrame:
    payload = load_json(path)
    audit = validate_neutral_pairwise_payload(payload)
    if not audit["valid"]:
        raise ValueError(f"Invalid neutral pairwise file {path}: {audit['issues'][:5]}")
    return pd.DataFrame(payload["evaluations"])


def choice_to_method(row: pd.Series, choice: str) -> str:
    if choice == "TIE":
        return "TIE"
    method = row["candidate_a_method"] if choice == "A" else row["candidate_b_method"]
    if method == METHOD_M1:
        return "M1"
    if method == METHOD_M3:
        return "M3"
    return method


def get_m1_m3_scores(row: pd.Series, metric: str):
    a = float(row["candidate_a_scores"][metric])
    b = float(row["candidate_b_scores"][metric])
    if row["candidate_a_method"] == METHOD_M1:
        return a, b
    return b, a


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--key", required=True)
    parser.add_argument("--judge", action="append", required=True, help="LABEL=/path/to/score-first-output.json")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--bootstrap", type=int, default=20000)
    parser.add_argument("--seed", type=int, default=20260623)
    args = parser.parse_args()

    key_path = Path(args.key).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    judge_specs = parse_judge_specs(args.judge)

    key = pd.read_csv(key_path)
    require_columns(
        key,
        ["pair_id", "candidate_a_method", "candidate_b_method", "domain", "source_idea_id"],
        "neutral pairwise private key",
    )
    if key["pair_id"].duplicated().any():
        raise ValueError("Neutral pairwise key contains duplicate pair_id.")

    audit_rows: List[Dict[str, Any]] = []
    score_rows: List[Dict[str, Any]] = []
    preference_rows: List[Dict[str, Any]] = []

    for judge, path in judge_specs:
        scores = load_and_validate(path)
        if set(scores["pair_id"]) != set(key["pair_id"]):
            missing = sorted(set(key["pair_id"]) - set(scores["pair_id"]))
            unexpected = sorted(set(scores["pair_id"]) - set(key["pair_id"]))
            raise ValueError(f"{judge}: pair ID mismatch; missing={missing[:10]}, unexpected={unexpected[:10]}")
        joined = scores.merge(key, on="pair_id", validate="one_to_one")

        rationales = joined["short_rationale"].astype(str).str.lower().str.replace(r"\s+", " ", regex=True).str.strip()
        profiles = [
            tuple(float(row["candidate_a_scores"][m]) for m in INDIVIDUAL_METRICS)
            + tuple(float(row["candidate_b_scores"][m]) for m in INDIVIDUAL_METRICS)
            for _, row in joined.iterrows()
        ]
        preference_profiles = [
            tuple(row["preferences"][PREFERENCE_MAP[m]] for m in INDIVIDUAL_METRICS)
            for _, row in joined.iterrows()
        ]
        audit_rows.append({
            "judge": judge,
            "source_file": path.name,
            "sha256": sha256_file(path),
            "n_pairs": len(joined),
            "unique_rationales": rationales.nunique(),
            "unique_score_profiles": len(set(profiles)),
            "unique_preference_profiles": len(set(preference_profiles)),
            "most_repeated_score_profile_count": int(pd.Series(profiles).value_counts().iloc[0]),
        })

        for _, row in joined.iterrows():
            for metric in INDIVIDUAL_METRICS:
                m1, m3 = get_m1_m3_scores(row, metric)
                score_rows.append({
                    "judge": judge,
                    "pair_id": row["pair_id"],
                    "domain": row["domain"],
                    "source_idea_id": row["source_idea_id"],
                    "metric": metric,
                    "M1_score": m1,
                    "M3_score": m3,
                    "delta_M3_minus_M1": m3 - m1,
                })
                pref_key = PREFERENCE_MAP[metric]
                choice = row["preferences"][pref_key]
                preference_rows.append({
                    "judge": judge,
                    "pair_id": row["pair_id"],
                    "domain": row["domain"],
                    "score_metric": metric,
                    "preference_metric": pref_key,
                    "public_choice": choice,
                    "unblinded_winner": choice_to_method(row, choice),
                })

    audit = pd.DataFrame(audit_rows)
    score_long = pd.DataFrame(score_rows)
    pref_long = pd.DataFrame(preference_rows)

    # Paired decimal score effects.
    paired_rows = []
    for judge in score_long["judge"].unique():
        for metric in INDIVIDUAL_METRICS:
            sub = score_long[(score_long["judge"] == judge) & (score_long["metric"] == metric)]
            d = sub["delta_M3_minus_M1"]
            w, p = safe_wilcoxon(d)
            low, high = bootstrap_mean_ci(d, args.bootstrap, args.seed)
            paired_rows.append({
                "judge": judge,
                "metric": metric,
                "n_pairs": len(sub),
                "M1_mean": sub["M1_score"].mean(),
                "M3_mean": sub["M3_score"].mean(),
                "mean_delta_M3_minus_M1": d.mean(),
                "median_delta_M3_minus_M1": d.median(),
                "bootstrap_95_low_mean_delta": low,
                "bootstrap_95_high_mean_delta": high,
                "M3_higher_count": int((d > 0).sum()),
                "M1_higher_count": int((d < 0).sum()),
                "exact_tie_count": int((d == 0).sum()),
                "wilcoxon_W": w,
                "wilcoxon_p_raw": p,
            })
    paired = pd.DataFrame(paired_rows)
    paired["wilcoxon_p_holm"] = np.nan
    for judge in paired["judge"].unique():
        idx = paired.index[paired["judge"] == judge]
        valid = paired.loc[idx, "wilcoxon_p_raw"].notna()
        if valid.any():
            paired.loc[idx[valid], "wilcoxon_p_holm"] = multipletests(
                paired.loc[idx[valid], "wilcoxon_p_raw"], method="holm"
            )[1]
    paired["significant_after_holm"] = paired["wilcoxon_p_holm"] < 0.05

    # Preference A/B/TIE results.
    pref_rows = []
    for judge in pref_long["judge"].unique():
        for metric in pref_long["score_metric"].unique():
            sub = pref_long[(pref_long["judge"] == judge) & (pref_long["score_metric"] == metric)]
            counts = sub["unblinded_winner"].value_counts()
            m3 = int(counts.get("M3", 0))
            m1 = int(counts.get("M1", 0))
            ties = int(counts.get("TIE", 0))
            n_non_tie = m3 + m1
            p = float(binomtest(m3, n_non_tie, 0.5, alternative="two-sided").pvalue) if n_non_tie else np.nan
            low, high = wilson_interval(m3, n_non_tie)
            pref_rows.append({
                "judge": judge,
                "metric": metric,
                "n_pairs": len(sub),
                "M3_wins": m3,
                "M1_wins": m1,
                "ties": ties,
                "M3_win_rate_all_pairs": m3 / len(sub),
                "M3_win_rate_excluding_ties": m3 / n_non_tie if n_non_tie else np.nan,
                "M3_wilson_95_low_excl_ties": low,
                "M3_wilson_95_high_excl_ties": high,
                "exact_binomial_p_raw": p,
            })
    pref_summary = pd.DataFrame(pref_rows)
    pref_summary["exact_binomial_p_holm"] = np.nan
    for judge in pref_summary["judge"].unique():
        idx = pref_summary.index[pref_summary["judge"] == judge]
        valid = pref_summary.loc[idx, "exact_binomial_p_raw"].notna()
        if valid.any():
            pref_summary.loc[idx[valid], "exact_binomial_p_holm"] = multipletests(
                pref_summary.loc[idx[valid], "exact_binomial_p_raw"], method="holm"
            )[1]
    pref_summary["significant_after_holm"] = pref_summary["exact_binomial_p_holm"] < 0.05

    # Cross judge concordance.
    concord_rows = []
    for metric in INDIVIDUAL_METRICS:
        sub = score_long[score_long["metric"] == metric]
        pivot = sub.pivot(index="pair_id", columns="judge", values="delta_M3_minus_M1")
        labels = list(pivot.columns)
        for i in range(len(labels)):
            for j in range(i + 1, len(labels)):
                x = pivot[[labels[i], labels[j]]].dropna()
                rho, p = spearmanr(x[labels[i]], x[labels[j]])
                concord_rows.append({
                    "kind": "paired_delta_spearman",
                    "metric": metric,
                    "judge_a": labels[i],
                    "judge_b": labels[j],
                    "n_pairs": len(x),
                    "value": rho,
                    "p_value": p,
                })

        pref = pref_long[pref_long["score_metric"] == metric].pivot(index="pair_id", columns="judge", values="public_choice")
        labels = list(pref.columns)
        for i in range(len(labels)):
            for j in range(i + 1, len(labels)):
                x = pref[[labels[i], labels[j]]].dropna()
                exact = float((x[labels[i]] == x[labels[j]]).mean())
                try:
                    kappa = cohen_kappa_score(x[labels[i]], x[labels[j]], labels=["A", "B", "TIE"])
                except Exception:
                    kappa = np.nan
                concord_rows.append({
                    "kind": "public_A_B_TIE_agreement",
                    "metric": metric,
                    "judge_a": labels[i],
                    "judge_b": labels[j],
                    "n_pairs": len(x),
                    "value": exact,
                    "p_value": kappa,
                })
    concordance = pd.DataFrame(concord_rows)

    # Save.
    pd.DataFrame([
        {"role": "private_pairwise_key", "file": key_path.name, "sha256": sha256_file(key_path)}
        ] + [
        {"role": f"judge_output::{label}", "file": p.name, "sha256": sha256_file(p)}
        for label, p in judge_specs
    ]).to_csv(out_dir / "00_input_manifest_sha256.csv", index=False)
    audit.to_csv(out_dir / "01_judge_output_quality_audit.csv", index=False)
    score_long.to_csv(out_dir / "02_unblinded_pairwise_decimal_scores_long.csv", index=False)
    paired.to_csv(out_dir / "03_paired_decimal_score_statistics.csv", index=False)
    pref_long.to_csv(out_dir / "04_unblinded_A_B_TIE_preferences_long.csv", index=False)
    pref_summary.to_csv(out_dir / "05_neutral_preference_summary.csv", index=False)
    concordance.to_csv(out_dir / "06_cross_judge_concordance.csv", index=False)

    (out_dir / "analysis_report.md").write_text(
        "# CHVD Neutral Pairwise Analysis\n\n"
        "This analysis is a randomized, method-blind A/B comparison. "
        "Judge-specific results are reported separately. Raw scores are not pooled across judges.\n",
        encoding="utf-8",
    )
    print(f"[OK] Wrote neutral pairwise analysis to {out_dir}")


if __name__ == "__main__":
    main()
