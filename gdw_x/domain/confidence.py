from __future__ import annotations

import math
from dataclasses import dataclass

from .contracts import DriftVector


def bounded(value: float) -> float:
    return min(1.0, max(0.0, value))


@dataclass(frozen=True)
class ConfidenceContext:
    raw_confidence: float
    data_quality: float
    regime_similarity: float
    execution_quality: float
    drift: DriftVector


@dataclass(frozen=True)
class ConfidenceResult:
    raw_confidence: float
    governed_confidence: float
    drift_penalty: float
    evidence_status: str = "MODELED"


def drift_penalty(drift: DriftVector, weights: dict[str, float]) -> float:
    values = drift.model_dump()
    score = sum(max(0.0, weights.get(name, 0.0)) * value for name, value in values.items())
    return math.exp(-score)


def runtime_confidence(
    context: ConfidenceContext,
    weights: dict[str, float],
) -> ConfidenceResult:
    penalty = drift_penalty(context.drift, weights)
    governed = (
        bounded(context.raw_confidence)
        * bounded(context.data_quality)
        * bounded(context.regime_similarity)
        * bounded(context.execution_quality)
        * penalty
    )
    return ConfidenceResult(
        raw_confidence=bounded(context.raw_confidence),
        governed_confidence=bounded(governed),
        drift_penalty=penalty,
    )
