from __future__ import annotations

import math
from typing import Iterable, Sequence, Tuple

import numpy as np
from scipy.stats import mannwhitneyu, wilcoxon


def bootstrap_mean_ci(values: Sequence[float], n_boot: int = 20000, seed: int = 20260623) -> Tuple[float, float]:
    arr = np.asarray(values, dtype=float)
    if len(arr) == 0:
        return (np.nan, np.nan)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(arr), size=(n_boot, len(arr)))
    means = arr[idx].mean(axis=1)
    return tuple(np.quantile(means, [0.025, 0.975]))


def safe_wilcoxon(deltas: Sequence[float]) -> Tuple[float, float]:
    arr = np.asarray(deltas, dtype=float)
    nonzero = arr[np.abs(arr) > 1e-12]
    if len(nonzero) == 0:
        return (np.nan, np.nan)
    result = wilcoxon(nonzero, alternative="two-sided", zero_method="wilcox", method="auto")
    return (float(result.statistic), float(result.pvalue))


def safe_mannwhitney(a: Sequence[float], b: Sequence[float]) -> Tuple[float, float]:
    arr_a = np.asarray(a, dtype=float)
    arr_b = np.asarray(b, dtype=float)
    if len(arr_a) == 0 or len(arr_b) == 0:
        return (np.nan, np.nan)
    result = mannwhitneyu(arr_a, arr_b, alternative="two-sided", method="auto")
    return (float(result.statistic), float(result.pvalue))


def wilson_interval(successes: int, n: int, z: float = 1.959963984540054) -> Tuple[float, float]:
    if n <= 0:
        return (np.nan, np.nan)
    p = successes / n
    denom = 1 + (z * z / n)
    center = (p + z * z / (2 * n)) / denom
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n) / denom
    return (center - margin, center + margin)
