from __future__ import annotations

import math
import statistics

from .contracts import CalibrationState, DriftVector

EPSILON = 1e-12


def standardized_mean_shift(reference: list[float], current: list[float]) -> float:
    if not reference or not current:
        raise ValueError("reference and current samples are required")
    scale = statistics.pstdev(reference)
    scale = scale if scale > EPSILON else 1.0
    return abs(statistics.fmean(current) - statistics.fmean(reference)) / scale


def residual_bias(residuals: list[float]) -> float:
    if not residuals:
        raise ValueError("residual samples are required")
    scale = max(statistics.pstdev(residuals), EPSILON)
    return abs(statistics.fmean(residuals)) / scale


def expected_calibration_error(
    confidence: list[float],
    outcomes: list[float],
    bins: int = 10,
) -> float:
    if len(confidence) != len(outcomes) or not confidence:
        raise ValueError("confidence and outcomes must be non-empty and aligned")
    if bins < 1:
        raise ValueError("bins must be positive")
    error = 0.0
    for index in range(bins):
        left = index / bins
        right = (index + 1) / bins
        members = [
            pair
            for pair in zip(confidence, outcomes)
            if pair[0] >= left and (pair[0] < right or (index == bins - 1 and pair[0] <= right))
        ]
        if not members:
            continue
        bucket_confidence = statistics.fmean(pair[0] for pair in members)
        bucket_outcome = statistics.fmean(pair[1] for pair in members)
        error += (len(members) / len(confidence)) * abs(bucket_outcome - bucket_confidence)
    return error


def parameter_distance(approved: dict[str, float], candidate: dict[str, float]) -> float:
    keys = sorted(set(approved) | set(candidate))
    if not keys:
        return 0.0
    baseline = [approved.get(key, 0.0) for key in keys]
    changed = [candidate.get(key, 0.0) for key in keys]
    denominator = max(math.sqrt(sum(value * value for value in baseline)), EPSILON)
    distance = math.sqrt(sum((right - left) ** 2 for left, right in zip(baseline, changed)))
    return distance / denominator


def calibration_state(drift: DriftVector) -> CalibrationState:
    maximum = max(drift.model_dump().values())
    if maximum >= 1.0 or drift.execution_drift >= 0.8:
        return CalibrationState.UNSAFE
    if maximum >= 0.6:
        return CalibrationState.DEGRADED
    if maximum >= 0.3:
        return CalibrationState.WATCH
    return CalibrationState.HEALTHY
