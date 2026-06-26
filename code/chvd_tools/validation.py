from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .constants import DIRECTIONAL_METRICS, INDIVIDUAL_METRICS, PREFERENCE_MAP, RISK_METRICS


def is_one_decimal(value: float) -> bool:
    return abs(value * 10 - round(value * 10)) < 1e-8


def check_score(value: Any) -> Optional[str]:
    try:
        numeric = float(value)
    except Exception:
        return "not_numeric"
    if not math.isfinite(numeric):
        return "not_finite"
    if numeric < 1.0 or numeric > 5.0:
        return "outside_1_to_5"
    if not is_one_decimal(numeric):
        return "not_one_decimal"
    return None


def validate_score_dict(
    score_dict: Any,
    expected_metrics: Sequence[str],
    record_label: str,
) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    if not isinstance(score_dict, dict):
        return [{
            "record": record_label,
            "field": "scores",
            "issue": "not_object",
            "value": repr(score_dict),
        }]

    observed = set(score_dict.keys())
    expected = set(expected_metrics)
    for metric in sorted(expected - observed):
        issues.append({
            "record": record_label,
            "field": "scores",
            "issue": "missing_metric",
            "value": metric,
        })
    for metric in sorted(observed - expected):
        issues.append({
            "record": record_label,
            "field": "scores",
            "issue": "unexpected_metric",
            "value": metric,
        })
    for metric in sorted(expected & observed):
        problem = check_score(score_dict[metric])
        if problem:
            issues.append({
                "record": record_label,
                "field": metric,
                "issue": problem,
                "value": score_dict[metric],
            })
    return issues


def infer_preference(score_a: float, score_b: float, lower_is_better: bool, tie_margin: float = 0.1) -> str:
    if abs(score_b - score_a) <= tie_margin + 1e-9:
        return "TIE"
    if lower_is_better:
        return "A" if score_a < score_b else "B"
    return "A" if score_a > score_b else "B"


def validate_individual_payload(payload: Mapping[str, Any]) -> Dict[str, Any]:
    issues: List[Dict[str, Any]] = []
    evaluations = payload.get("evaluations")
    if not isinstance(evaluations, list):
        return {
            "valid": False,
            "schema": "individual",
            "n_records": 0,
            "issues": [{"record": "root", "field": "evaluations", "issue": "missing_or_not_list", "value": repr(evaluations)}],
        }

    candidate_ids = []
    for index, item in enumerate(evaluations):
        record = f"evaluations[{index}]"
        if not isinstance(item, dict):
            issues.append({"record": record, "field": "record", "issue": "not_object", "value": repr(item)})
            continue
        candidate_id = item.get("candidate_id")
        if not isinstance(candidate_id, str) or not candidate_id.strip():
            issues.append({"record": record, "field": "candidate_id", "issue": "missing_or_blank", "value": candidate_id})
        else:
            candidate_ids.append(candidate_id)
        issues.extend(validate_score_dict(item.get("scores"), INDIVIDUAL_METRICS, record))
        if not isinstance(item.get("short_rationale", ""), str):
            issues.append({"record": record, "field": "short_rationale", "issue": "not_string", "value": repr(item.get("short_rationale"))})

    for cid in sorted({x for x in candidate_ids if candidate_ids.count(x) > 1}):
        issues.append({"record": "all", "field": "candidate_id", "issue": "duplicate_id", "value": cid})

    return {
        "valid": len(issues) == 0,
        "schema": "individual",
        "n_records": len(evaluations),
        "unique_ids": len(set(candidate_ids)),
        "issues": issues,
    }


def validate_neutral_pairwise_payload(
    payload: Mapping[str, Any],
    strict_preference_consistency: bool = True,
    tie_margin: float = 0.1,
) -> Dict[str, Any]:
    issues: List[Dict[str, Any]] = []
    evaluations = payload.get("evaluations")
    if not isinstance(evaluations, list):
        return {
            "valid": False,
            "schema": "neutral_pairwise_score_first",
            "n_records": 0,
            "issues": [{"record": "root", "field": "evaluations", "issue": "missing_or_not_list", "value": repr(evaluations)}],
        }

    pair_ids = []
    for index, item in enumerate(evaluations):
        record = f"evaluations[{index}]"
        if not isinstance(item, dict):
            issues.append({"record": record, "field": "record", "issue": "not_object", "value": repr(item)})
            continue

        pair_id = item.get("pair_id")
        if not isinstance(pair_id, str) or not pair_id.strip():
            issues.append({"record": record, "field": "pair_id", "issue": "missing_or_blank", "value": pair_id})
        else:
            pair_ids.append(pair_id)

        issues.extend(validate_score_dict(item.get("candidate_a_scores"), INDIVIDUAL_METRICS, record + ".candidate_a_scores"))
        issues.extend(validate_score_dict(item.get("candidate_b_scores"), INDIVIDUAL_METRICS, record + ".candidate_b_scores"))

        preferences = item.get("preferences")
        if not isinstance(preferences, dict):
            issues.append({"record": record, "field": "preferences", "issue": "missing_or_not_object", "value": repr(preferences)})
            continue

        expected_pref_keys = set(PREFERENCE_MAP.values())
        for key in sorted(expected_pref_keys - set(preferences.keys())):
            issues.append({"record": record, "field": "preferences", "issue": "missing_preference", "value": key})
        for key in sorted(set(preferences.keys()) - expected_pref_keys):
            issues.append({"record": record, "field": "preferences", "issue": "unexpected_preference", "value": key})

        if isinstance(item.get("candidate_a_scores"), dict) and isinstance(item.get("candidate_b_scores"), dict):
            for metric in INDIVIDUAL_METRICS:
                pref_key = PREFERENCE_MAP[metric]
                observed = preferences.get(pref_key)
                if observed not in {"A", "B", "TIE"}:
                    issues.append({"record": record, "field": pref_key, "issue": "invalid_preference_label", "value": observed})
                    continue
                if metric in item["candidate_a_scores"] and metric in item["candidate_b_scores"] and strict_preference_consistency:
                    try:
                        expected = infer_preference(
                            float(item["candidate_a_scores"][metric]),
                            float(item["candidate_b_scores"][metric]),
                            lower_is_better=(metric in RISK_METRICS),
                            tie_margin=tie_margin,
                        )
                        if observed != expected:
                            issues.append({
                                "record": record,
                                "field": pref_key,
                                "issue": "preference_not_consistent_with_scores",
                                "value": f"observed={observed};expected={expected}",
                            })
                    except Exception:
                        pass

        if not isinstance(item.get("short_rationale", ""), str):
            issues.append({"record": record, "field": "short_rationale", "issue": "not_string", "value": repr(item.get("short_rationale"))})

    for pid in sorted({x for x in pair_ids if pair_ids.count(x) > 1}):
        issues.append({"record": "all", "field": "pair_id", "issue": "duplicate_id", "value": pid})

    return {
        "valid": len(issues) == 0,
        "schema": "neutral_pairwise_score_first",
        "n_records": len(evaluations),
        "unique_ids": len(set(pair_ids)),
        "issues": issues,
    }


def validate_directional_payload(payload: Mapping[str, Any]) -> Dict[str, Any]:
    issues: List[Dict[str, Any]] = []
    evaluations = payload.get("evaluations")
    if not isinstance(evaluations, list):
        return {
            "valid": False,
            "schema": "directional_traceability",
            "n_records": 0,
            "issues": [{"record": "root", "field": "evaluations", "issue": "missing_or_not_list", "value": repr(evaluations)}],
        }

    pair_ids = []
    for index, item in enumerate(evaluations):
        record = f"evaluations[{index}]"
        if not isinstance(item, dict):
            issues.append({"record": record, "field": "record", "issue": "not_object", "value": repr(item)})
            continue

        pair_id = item.get("pair_id")
        if not isinstance(pair_id, str) or not pair_id.strip():
            issues.append({"record": record, "field": "pair_id", "issue": "missing_or_blank", "value": pair_id})
        else:
            pair_ids.append(pair_id)

        issues.extend(validate_score_dict(item.get("scores"), DIRECTIONAL_METRICS, record + ".scores"))

        choice = item.get("preferred_for_prototype")
        if choice not in {"REFERENCE", "TRANSFORMATION", "TIE"}:
            issues.append({
                "record": record,
                "field": "preferred_for_prototype",
                "issue": "invalid_prototype_choice",
                "value": choice,
            })

        if not isinstance(item.get("short_rationale", ""), str):
            issues.append({"record": record, "field": "short_rationale", "issue": "not_string", "value": repr(item.get("short_rationale"))})

    for pid in sorted({x for x in pair_ids if pair_ids.count(x) > 1}):
        issues.append({"record": "all", "field": "pair_id", "issue": "duplicate_id", "value": pid})

    return {
        "valid": len(issues) == 0,
        "schema": "directional_traceability",
        "n_records": len(evaluations),
        "unique_ids": len(set(pair_ids)),
        "issues": issues,
    }
