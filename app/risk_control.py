from __future__ import annotations

import math
from typing import Any


def conformal_risk_threshold(
    nonconformity_scores: list[float],
    *,
    alpha: float,
    minimum_samples: int = 20,
) -> dict[str, Any]:
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be between zero and one")
    if minimum_samples < 1:
        raise ValueError("minimum_samples must be positive")
    if any(score < 0.0 or score > 1.0 for score in nonconformity_scores):
        raise ValueError("nonconformity scores must be between zero and one")
    sample_count = len(nonconformity_scores)
    if sample_count < minimum_samples:
        return {
            "threshold": None,
            "sample_count": sample_count,
            "minimum_samples": minimum_samples,
            "target_coverage": 1.0 - alpha,
            "evidence_status": "UNAVAILABLE",
            "assumptions": [
                "insufficient held-out calibration observations",
                "no finite-sample operating threshold emitted",
            ],
        }

    ordered = sorted(nonconformity_scores)
    rank = min(sample_count, math.ceil((sample_count + 1) * (1.0 - alpha)))
    return {
        "threshold": ordered[rank - 1],
        "sample_count": sample_count,
        "minimum_samples": minimum_samples,
        "target_coverage": 1.0 - alpha,
        "evidence_status": "MEASURED",
        "assumptions": [
            "scores are from a held-out calibration set",
            "exchangeability is required for the finite-sample coverage interpretation",
            "distribution drift invalidates the interpretation until recalibration",
        ],
    }
