from __future__ import annotations

import math
from typing import Any


def conformal_risk_threshold(
    nonconformity_scores: list[float],
    *,
    alpha: float,
    minimum_samples: int = 20,
) -> dict[str, Any]:
    if not math.isfinite(alpha) or not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be finite and between zero and one")
    if isinstance(minimum_samples, bool) or not isinstance(minimum_samples, int) or minimum_samples < 1:
        raise ValueError("minimum_samples must be a positive integer")
    if any(not math.isfinite(score) or not 0.0 <= score <= 1.0 for score in nonconformity_scores):
        raise ValueError("nonconformity scores must be finite and between zero and one")
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

    rank = math.ceil((sample_count + 1) * (1.0 - alpha))
    if rank > sample_count:
        return {
            "threshold": None,
            "sample_count": sample_count,
            "minimum_samples": minimum_samples,
            "target_coverage": 1.0 - alpha,
            "evidence_status": "UNAVAILABLE",
            "assumptions": [
                "requested coverage requires a rank beyond the calibration sample",
                "no finite-sample operating threshold emitted; obtain more held-out observations",
            ],
        }

    ordered = sorted(nonconformity_scores)
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
