"""Validation helpers for anonymized CHVD human-evaluation data."""
from __future__ import annotations

from typing import Iterable

import pandas as pd

REQUIRED_LONG_COLUMNS = {"participant_id", "pair_id", "condition", "metric", "score"}
VALID_SCORES = {1, 2, 3, 4, 5}


def validate_long_human_scores(
    frame: pd.DataFrame,
    allowed_conditions: Iterable[str] = ("M1", "M3"),
) -> list[str]:
    """Return schema/privacy issues for a public long-format human-score file."""
    errors: list[str] = []
    missing = REQUIRED_LONG_COLUMNS.difference(frame.columns)
    if missing:
        errors.append(f"Missing required columns: {sorted(missing)}")
        return errors
    if frame.empty:
        errors.append("Dataset is empty.")
        return errors
    if frame[list(REQUIRED_LONG_COLUMNS)].isnull().any().any():
        errors.append("Required fields contain missing values.")
    conditions = set(frame["condition"].astype(str))
    unexpected = conditions.difference(set(allowed_conditions))
    if unexpected:
        errors.append(f"Unexpected condition labels: {sorted(unexpected)}")
    numeric = pd.to_numeric(frame["score"], errors="coerce")
    if numeric.isna().any() or not numeric.isin(VALID_SCORES).all():
        errors.append("Scores must be integers in {1, 2, 3, 4, 5}.")
    duplicate_keys = frame.duplicated(
        ["participant_id", "pair_id", "condition", "metric"], keep=False
    )
    if duplicate_keys.any():
        errors.append("Duplicate participant/pair/condition/metric rows found.")
    forbidden = {"name", "email", "student_id", "phone", "exact_timestamp"}
    present = forbidden.intersection({str(column).lower() for column in frame.columns})
    if present:
        errors.append(f"Potentially identifying public columns present: {sorted(present)}")
    return errors
