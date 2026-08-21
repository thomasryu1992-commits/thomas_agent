"""Feature distribution shift gate — refuse entries when current conditions
diverge from the backtest training distribution.

A strategy backtested in a trending, moderate-volatility market has no evidence
for a regime its numbers were never trained on.  The regime gate (``paper.regime_admits``)
catches the discrete label; this gate catches the *continuous* shift that happens
within a label — volatility doubling while the label stays "TREND_UP", or ADX
falling to a level the backtest window never saw.

**The metric is the mean absolute z-score** of a fixed set of features, measured
against the mean and standard deviation of those same features over the backtest's
scored window.  It is interpretable (a DI of 3.0 means current features are on
average 3 standard deviations from training), pure, and needs no matrix inversion.

**Fail-open on absent reference**: strategies promoted before this gate existed carry
no ``distribution_reference`` and are admitted without a check — the same posture as
``regime_admits`` with absent ``regime_evidence``.
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

DI_FEATURES: tuple[str, ...] = (
    "atr",
    "adx",
    "rsi",
    "bb_width_percentile",
    "momentum_z",
    "vol_ratio",
)

DI_THRESHOLD = 3.0

MIN_REFERENCE_BARS = 30


def compute_distribution_reference(
    rows: Sequence[Mapping[str, Any]],
    features: Sequence[str] = DI_FEATURES,
) -> dict[str, dict[str, float]]:
    """Mean and standard deviation of each feature over a scored window.

    Returns ``{feature: {"mean": ..., "std": ..., "n": ...}}`` for every feature
    that has enough finite observations.  A feature with fewer than
    ``MIN_REFERENCE_BARS`` observations is omitted — there is nothing to compare
    against.
    """
    ref: dict[str, dict[str, float]] = {}
    for feature in features:
        values = [
            float(row[feature]) for row in rows
            if feature in row
            and isinstance(row[feature], (int, float))
            and not isinstance(row[feature], bool)
            and math.isfinite(float(row[feature]))
        ]
        if len(values) < MIN_REFERENCE_BARS:
            continue
        mean = sum(values) / len(values)
        variance = sum((v - mean) ** 2 for v in values) / len(values)
        std = math.sqrt(variance)
        ref[feature] = {"mean": round(mean, 8), "std": round(std, 8), "n": len(values)}
    return ref


def dissimilarity_index(
    current_row: Mapping[str, Any],
    reference: Mapping[str, Mapping[str, float]],
) -> float | None:
    """Mean absolute z-score of the current row against the reference distribution.

    Returns ``None`` when no feature could be scored (reference empty or current
    row missing every tracked feature).
    """
    z_scores: list[float] = []
    for feature, stats in reference.items():
        value = current_row.get(feature)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        std = float(stats.get("std") or 0.0)
        if std <= 0:
            continue
        z = abs((float(value) - float(stats["mean"])) / std)
        z_scores.append(z)
    if not z_scores:
        return None
    return round(sum(z_scores) / len(z_scores), 6)


DISTRIBUTION_SHIFT = "distribution_shift_detected"


def distribution_admits(
    entry: Mapping[str, Any],
    current_row: Mapping[str, Any],
    threshold: float = DI_THRESHOLD,
) -> tuple[bool, str | None, float | None]:
    """May this pool entry trade given the current feature distribution?

    Returns ``(admitted, reason, di_score)``.  Fail-open on absent reference
    (strategies promoted before this gate existed).
    """
    reference = entry.get("distribution_reference")
    if not isinstance(reference, Mapping) or not reference:
        return True, None, None
    di = dissimilarity_index(current_row, reference)
    if di is None:
        return True, None, None
    if di > threshold:
        return False, DISTRIBUTION_SHIFT, di
    return True, None, di


__all__ = [
    "DI_FEATURES",
    "DI_THRESHOLD",
    "DISTRIBUTION_SHIFT",
    "MIN_REFERENCE_BARS",
    "compute_distribution_reference",
    "dissimilarity_index",
    "distribution_admits",
]
