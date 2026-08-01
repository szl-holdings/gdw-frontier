from __future__ import annotations

import math
from statistics import median

from gdw_x.domain.master_suite import (
    DriftLocalization,
    DriftVector,
    FormulaObservation,
    canonical_hash,
)


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def _quantile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(probability * len(ordered)) - 1))
    return ordered[index]


def _robust_scores(values: list[float]) -> tuple[float, float, list[float]]:
    center = median(values)
    absolute_deviations = [abs(value - center) for value in values]
    scale = max(1.4826 * median(absolute_deviations), 1e-9)
    return center, scale, [abs(value - center) / scale for value in values]


class DASCLocalizer:
    """A bounded, empirical conformal drift-localization wrapper.

    This is an engineering interpretation of DASC-style localization, not a
    reproduction of the paper and not a formal coverage guarantee.
    """

    def __init__(
        self,
        *,
        alpha: float = 0.10,
        minimum_reference_size: int = 20,
        unsafe_threshold: float = 0.80,
    ) -> None:
        self.alpha = _clamp(alpha, 0.01, 0.25)
        self.minimum_reference_size = max(5, minimum_reference_size)
        self.unsafe_threshold = _clamp(unsafe_threshold, 0.50, 0.99)

    def localize(
        self,
        observation: FormulaObservation,
        reference_pool: list[FormulaObservation],
    ) -> DriftLocalization:
        comparable = [
            item
            for item in reference_pool
            if item.formula_id == observation.formula_id
            and item.feature_schema_hash == observation.feature_schema_hash
            and item.timestamp <= observation.timestamp
        ]
        dimensions: dict[str, dict[str, float | int | str]] = {}
        drift_scores: list[float] = []

        for name, observed in sorted(observation.features.items()):
            reference = [
                item.features[name]
                for item in comparable
                if name in item.features
            ]
            if len(reference) < 5:
                dimensions[name] = {
                    "status": "insufficient_reference",
                    "reference_n": len(reference),
                    "observed": observed,
                    "p_value": 0.0,
                    "severity": 1.0,
                }
                drift_scores.append(1.0)
                continue

            center, scale, calibration_scores = _robust_scores(reference)
            test_score = abs(observed - center) / scale
            tail_count = sum(score >= test_score for score in calibration_scores)
            p_value = (tail_count + 1.0) / (len(calibration_scores) + 1.0)
            severity = _clamp(1.0 - p_value)
            dimensions[name] = {
                "status": "localized",
                "reference_n": len(reference),
                "observed": observed,
                "reference_median": center,
                "robust_scale": scale,
                "nonconformity": test_score,
                "p_value": p_value,
                "severity": severity,
            }
            drift_scores.append(severity)

        residuals = [
            abs(item.realized - item.predicted)
            for item in comparable
            if item.realized is not None
        ]
        maximum_drift = max(drift_scores, default=1.0)
        adaptive_alpha = max(0.005, self.alpha * (1.0 - 0.75 * maximum_drift))
        residual_quantile = _quantile(residuals, 1.0 - adaptive_alpha)
        empirical_coverage = None
        if residual_quantile is not None and residuals:
            empirical_coverage = sum(
                residual <= residual_quantile for residual in residuals
            ) / len(residuals)

        evidence_quality = _clamp(
            len(comparable) / float(self.minimum_reference_size)
        )
        calibrated_confidence = _clamp(
            observation.raw_confidence
            * (1.0 - 0.70 * maximum_drift)
            * (0.50 + 0.50 * evidence_quality)
        )
        interval_lower = (
            observation.predicted - residual_quantile
            if residual_quantile is not None
            else None
        )
        interval_upper = (
            observation.predicted + residual_quantile
            if residual_quantile is not None
            else None
        )
        abstain = (
            evidence_quality < 1.0
            or maximum_drift >= self.unsafe_threshold
            or residual_quantile is None
        )

        if evidence_quality < 1.0:
            alert_family = "stale_calibration_pool"
            next_step = "recalibrate"
        elif (
            observation.raw_confidence - calibrated_confidence >= 0.30
            and maximum_drift < self.unsafe_threshold
        ):
            alert_family = "confidence_anomaly"
            next_step = "simulate_and_review"
        elif maximum_drift >= self.unsafe_threshold:
            alert_family = "conformal_unsafe"
            next_step = "quarantine_or_rollback"
        elif maximum_drift >= 0.55:
            alert_family = "conformal_degraded"
            next_step = "simulate_and_review"
        elif maximum_drift >= 0.25:
            alert_family = "conformal_watch"
            next_step = "monitor"
        else:
            alert_family = "none"
            next_step = "continue"

        coverage_deviation = (
            abs((1.0 - self.alpha) - empirical_coverage)
            if empirical_coverage is not None
            else None
        )
        drift = DriftVector(
            input_drift=maximum_drift,
            residual_drift=_clamp(
                coverage_deviation
                if coverage_deviation is not None
                else 1.0
            ),
            calibration_drift=_clamp(
                observation.raw_confidence - calibrated_confidence
            ),
            regime_novelty=maximum_drift,
            localization_payload={
                "method": "empirical_conformal_robust_nonconformity",
                "dimension_count": len(dimensions),
                "adaptive_alpha": adaptive_alpha,
            },
        )
        limitations = [
            "MODELED engineering wrapper; not a reproduction of arXiv:2602.19790",
            "Finite-sample coverage is not asserted under non-exchangeable drift",
            "No runtime eligibility is granted when the reference pool is insufficient",
        ]
        return DriftLocalization(
            reference_window_hash=canonical_hash(
                [item.model_dump(mode="json") for item in comparable]
            ),
            reference_size=len(comparable),
            effective_sample_size=float(len(comparable)),
            evidence_quality=evidence_quality,
            dimensions=dimensions,
            drift=drift,
            interval_lower=interval_lower,
            interval_upper=interval_upper,
            interval_width=(
                2.0 * residual_quantile
                if residual_quantile is not None
                else None
            ),
            target_coverage=1.0 - self.alpha,
            empirical_coverage=empirical_coverage,
            coverage_deviation=coverage_deviation,
            calibrated_confidence=calibrated_confidence,
            abstain=abstain,
            alert_family=alert_family,
            recommended_next_step=next_step,
            limitations=limitations,
        )
