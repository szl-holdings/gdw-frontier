from __future__ import annotations

from collections.abc import Mapping
from typing import Any

DEFAULT_WEIGHTS = {
    "latency": 1.0,
    "error_rate": 1.0,
    "missing_receipts": 1.0,
    "cardinality": 1.0,
    "weight_validation": 1.0,
}


def _validated_weights(overrides: Mapping[str, float] | None) -> dict[str, float]:
    weights = dict(DEFAULT_WEIGHTS)
    if overrides:
        unknown = sorted(set(overrides) - set(weights))
        if unknown:
            raise ValueError(f"unknown release-score factors: {', '.join(unknown)}")
        weights.update({key: float(value) for key, value in overrides.items()})
    if any(value < 0.0 for value in weights.values()):
        raise ValueError("release-score weights must be non-negative")
    return weights


def release_score_breakdown(
    p95_latency_ms: float,
    error_rate: float,
    missing_receipt_rate: float,
    weight_validation_passed: bool,
    cardinality_risk: float,
    weights: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    factors = _validated_weights(weights)
    penalties = {
        "latency": min(40.0, max(0.0, p95_latency_ms) / 100.0) * factors["latency"],
        "error_rate": min(25.0, max(0.0, error_rate) * 500.0) * factors["error_rate"],
        "missing_receipts": min(25.0, max(0.0, missing_receipt_rate) * 1000.0)
        * factors["missing_receipts"],
        "cardinality": min(10.0, max(0.0, cardinality_risk) * 100.0) * factors["cardinality"],
        "weight_validation": (0.0 if weight_validation_passed else 50.0) * factors["weight_validation"],
    }
    return {
        "score": max(0.0, 100.0 - sum(penalties.values())),
        "penalties": penalties,
        "weights": factors,
    }


def release_score(
    p95_latency_ms: float,
    error_rate: float,
    missing_receipt_rate: float,
    weight_validation_passed: bool,
    cardinality_risk: float,
    weights: Mapping[str, float] | None = None,
) -> float:
    return float(
        release_score_breakdown(
            p95_latency_ms=p95_latency_ms,
            error_rate=error_rate,
            missing_receipt_rate=missing_receipt_rate,
            weight_validation_passed=weight_validation_passed,
            cardinality_risk=cardinality_risk,
            weights=weights,
        )["score"]
    )
