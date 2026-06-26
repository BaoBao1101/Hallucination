#!/usr/bin/env python3
"""
Analyze CHVD role-aware, method-blind directional traceability evaluations.

This is not a neutral A/B experiment. The evaluator knows reference versus
transformation role, so prototype preference is a directional recommendation.
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

from chvd_tools.constants import DIRECTIONAL_METRICS
from chvd_tools.io_utils import bool_from_csv, load_json, parse_judge_specs, require_columns, sha256_file
from chvd_tools.stats_utils import bootstrap_mean_ci, safe_wilcoxon, wilson_interval
from chvd_tools.validation import validate_directional_payload


def load_and_validate(path: Path) -> pd.DataFrame:
    payload = load_json(path)
    audit = validate_directional_payload(payload)
    if not audit["valid"]:
        raise ValueError(f"Invalid directional file {path}: {audit['issues'][:5]}")
    return pd.DataFrame(payload["evaluations"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--key", required=True)
    parser.add_argument("--judge", action="append", required=True, help="LABEL=/path/to/directional.json")
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
        [
            "pair_id",
            "domain",
            "source_idea_id",
            "reference_hidden_method",
            "transformation_hidden_method",
            "reference_displayed_first",
        ],
        "directional private key",
    )
    if key["pair_id"].duplicated().any():
        raise ValueError("Directional key contains duplicate pair_id.")
    if not set(key["reference_hidden_method"]).issubset({"M1_RAW_HIGH_TEMP", "M0_DIRECT_LOW_TEMP"}):
        raise ValueError("Unexpected reference method in directional key.")
    if not set(key["transformation_hidden_method"]).issubset({"M3_CHVD_DISTILLED"}):
        raise ValueError("Unexpected transformation method in directional key.")

    audit_rows: List[Dict[str, Any]] = []
    long_rows: List[Dict[str, Any]] = []
    proto_rows: List[Dict[str, Any]] = []

    for judge, path in judge_specs:
        output = load_and_validate(path)
        if set(output["pair_id"]) != set(key["pair_id"]):
            missing = sorted(set(key["pair_id"]) - set(output["pair_id"]))
            unexpected = sorted(set(output["pair_id"]) - set(key["pair_id"]))
            raise ValueError(f"{judge}: pair ID mismatch; missing={missing[:10]}, unexpected={unexpected[:10]}")
        joined = output.merge(key, on="pair_id", validate="one_to_one")

        rationales = joined["short_rationale"].astype(str).str.lower().str.replace(r"\s+", " ", regex=True).str.strip()
        profiles = [tuple(float(row["scores"][m]) for m in DIRECTIONAL_METRICS) for _, row in joined.iterrows()]
        audit_rows.append({
            "judge": judge,
            "source_file": path.name,
            "sha256": sha256_file(path),
            "n_pairs": len(joined),
            "unique_rationales": rationales.nunique(),
            "most_repeated_rationale_count": int(rationales.value_counts().iloc[0]),
            "unique_5_metric_profiles": len(set(profiles)),
            "most_repeated_profile_count": int(pd.Series(profiles).value_counts().iloc[0]),
        })

        for _, row in joined.iterrows():
            for metric in DIRECTIONAL_METRICS:
                long_rows.append({
                    "judge": judge,
                    "pair_id": row["pair_id"],
                    "domain": row["domain"],
                    "source_idea_id": row["source_idea_id"],
                    "metric": metric,
                    "score": float(row["scores"][metric]),
                    "reference_displayed_first": bool_from_csv(row["reference_displayed_first"]),
                })
            proto_rows.append({
                "judge": judge,
                "pair_id": row["pair_id"],
                "domain": row["domain"],
                "source_idea_id": row["source_idea_id"],
                "preferred_for_prototype": row["preferred_for_prototype"],
                "reference_displayed_first": bool_from_csv(row["reference_displayed_first"]),
            })

    audit = pd.DataFrame(audit_rows)
    long = pd.DataFrame(long_rows)
    proto = pd.DataFrame(proto_rows)

    summary_rows = []
    for judge in long["judge"].unique():
        for metric in DIRECTIONAL_METRICS:
            s = long[(long["judge"] == judge) & (long["metric"] == metric)]["score"]
            low, high = bootstrap_mean_ci(s, args.bootstrap, args.seed)
            summary_rows.append({
                "judge": judge,
                "metric": metric,
                "n_pairs": len(s),
                "mean": s.mean(),
                "sd": s.std(ddof=1),
                "median": s.median(),
                "q1": s.quantile(.25),
                "q3": s.quantile(.75),
                "bootstrap_95_low_mean": low,
                "bootstrap_95_high_mean": high,
                "score_ge_4_count": int((s >= 4).sum()),
                "score_le_2_count": int((s <= 2).sum()),
            })
    summary = pd.DataFrame(summary_rows)

    proto_rows2 = []
    for judge in proto["judge"].unique():
        s = proto[proto["judge"] == judge]["preferred_for_prototype"]
        counts = s.value_counts()
        t = int(counts.get("TRANSFORMATION", 0))
        r = int(counts.get("REFERENCE", 0))
        tie = int(counts.get("TIE", 0))
        n_non_tie = t + r
        p = float(binomtest(t, n_non_tie, .5, alternative="two-sided").pvalue) if n_non_tie else np.nan
        lo, hi = wilson_interval(t, n_non_tie)
        proto_rows2.append({
            "judge": judge,
            "n_pairs": len(s),
            "transformation_preferred": t,
            "reference_preferred": r,
            "ties": tie,
            "transformation_share_excluding_ties": t / n_non_tie if n_non_tie else np.nan,
            "wilson_95_low": lo,
            "wilson_95_high": hi,
            "directional_exact_binomial_p": p,
        })
    proto_summary = pd.DataFrame(proto_rows2)

    # Judge score rank agreement plus calibration test.
    concord_rows = []
    calibration_rows = []
    for metric in DIRECTIONAL_METRICS:
        sub = long[long["metric"] == metric].pivot(index="pair_id", columns="judge", values="score")
        labels = list(sub.columns)
        for i in range(len(labels)):
            for j in range(i + 1, len(labels)):
                x = sub[[labels[i], labels[j]]].dropna()
                rho, p = spearmanr(x[labels[i]], x[labels[j]])
                delta = x[labels[j]] - x[labels[i]]
                w, p_wil = safe_wilcoxon(delta)
                lo, hi = bootstrap_mean_ci(delta, args.bootstrap, args.seed)
                concord_rows.append({
                    "metric": metric,
                    "judge_a": labels[i],
                    "judge_b": labels[j],
                    "n_pairs": len(x),
                    "spearman_rho": rho,
                    "spearman_p": p,
                    "exact_score_agreement": float((x[labels[i]] == x[labels[j]]).mean()),
                    "mean_absolute_difference": float(np.abs(delta).mean()),
                })
                calibration_rows.append({
                    "metric": metric,
                    "judge_a": labels[i],
                    "judge_b": labels[j],
                    "mean_judge_b_minus_judge_a": delta.mean(),
                    "bootstrap_95_low": lo,
                    "bootstrap_95_high": hi,
                    "wilcoxon_W": w,
                    "wilcoxon_p_raw": p_wil,
                })
    concordance = pd.DataFrame(concord_rows)
    calibration = pd.DataFrame(calibration_rows)
    if not calibration.empty:
        calibration["wilcoxon_p_holm"] = multipletests(calibration["wilcoxon_p_raw"], method="holm")[1]
        calibration["significant_after_holm"] = calibration["wilcoxon_p_holm"] < .05

    # Prototype agreement between judges.
    proto_agreement_rows = []
    pivot = proto.pivot(index="pair_id", columns="judge", values="preferred_for_prototype")
    labels = list(pivot.columns)
    for i in range(len(labels)):
        for j in range(i + 1, len(labels)):
            x = pivot[[labels[i], labels[j]]].dropna()
            exact = float((x[labels[i]] == x[labels[j]]).mean())
            try:
                kappa = cohen_kappa_score(x[labels[i]], x[labels[j]], labels=["REFERENCE", "TRANSFORMATION", "TIE"])
            except Exception:
                kappa = np.nan
            proto_agreement_rows.append({
                "judge_a": labels[i],
                "judge_b": labels[j],
                "n_pairs": len(x),
                "exact_agreement": exact,
                "cohen_kappa": kappa,
                "disagreement_count": int((x[labels[i]] != x[labels[j]]).sum()),
            })
    proto_agreement = pd.DataFrame(proto_agreement_rows)

    display = (
        proto.groupby(["judge", "reference_displayed_first", "preferred_for_prototype"], as_index=False)
        .size()
        .rename(columns={"size": "count"})
    )

    pd.DataFrame([
        {"role": "private_directional_key", "file": key_path.name, "sha256": sha256_file(key_path)}
        ] + [
        {"role": f"judge_output::{label}", "file": p.name, "sha256": sha256_file(p)}
        for label, p in judge_specs
    ]).to_csv(out_dir / "00_input_manifest_sha256.csv", index=False)
    audit.to_csv(out_dir / "01_judge_output_quality_audit.csv", index=False)
    long.to_csv(out_dir / "02_directional_scores_long.csv", index=False)
    summary.to_csv(out_dir / "03_directional_metric_summary.csv", index=False)
    proto.to_csv(out_dir / "04_directional_prototype_preferences.csv", index=False)
    proto_summary.to_csv(out_dir / "05_directional_prototype_summary.csv", index=False)
    concordance.to_csv(out_dir / "06_cross_judge_score_concordance.csv", index=False)
    calibration.to_csv(out_dir / "07_cross_judge_calibration.csv", index=False)
    proto_agreement.to_csv(out_dir / "08_cross_judge_prototype_agreement.csv", index=False)
    display.to_csv(out_dir / "09_display_order_diagnostic.csv", index=False)

    (out_dir / "analysis_report.md").write_text(
        "# CHVD Directional Traceability Analysis\n\n"
        "This is role-aware and method-blind. It assesses traceability, preservation, "
        "overclaim removal, and semantic drift; it is not neutral A/B preference evidence.\n",
        encoding="utf-8",
    )
    print(f"[OK] Wrote directional traceability analysis to {out_dir}")


if __name__ == "__main__":
    main()
