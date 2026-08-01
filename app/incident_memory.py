from __future__ import annotations

import math
from datetime import timezone
from typing import Any

from .diagnostics import DiagnosticSnapshot


def vec(snapshot: DiagnosticSnapshot) -> list[float]:
    """Scale heterogeneous telemetry before similarity comparison."""
    return [
        snapshot.p50_latency_ms / 500.0,
        snapshot.p95_latency_ms / 1_500.0,
        snapshot.p99_latency_ms / 3_000.0,
        snapshot.error_rate,
        float(snapshot.inflight_requests) / 1_000.0,
        snapshot.missing_receipt_rate,
        float(snapshot.prometheus_series_count or 0) / 500_000.0,
        float(snapshot.log_volume_per_min or 0) / 10_000.0,
        1.0 if snapshot.weight_validation_passed else 0.0,
    ]


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def find_similar(
    snapshot: DiagnosticSnapshot,
    exemplars: list[dict[str, Any]],
    k: int = 5,
) -> list[tuple[float, dict[str, Any]]]:
    query = vec(snapshot)
    scored: list[tuple[float, dict[str, Any]]] = []
    for row in exemplars:
        try:
            candidate = DiagnosticSnapshot.model_validate(row["snapshot"])
        except (KeyError, TypeError, ValueError):
            continue
        trust = min(1.0, max(0.0, float(row.get("trust_score", 1.0))))
        risk = min(1.0, max(0.0, float(row.get("risk_score", 0.0))))
        numeric_similarity = cosine(query, vec(candidate))
        recency = 1.0
        if snapshot.observed_at and candidate.observed_at:
            query_time = snapshot.observed_at
            candidate_time = candidate.observed_at
            if query_time.tzinfo is None:
                query_time = query_time.replace(tzinfo=timezone.utc)
            if candidate_time.tzinfo is None:
                candidate_time = candidate_time.replace(tzinfo=timezone.utc)
            age_days = abs((query_time - candidate_time).total_seconds()) / 86_400.0
            recency = 0.5 + 0.5 * math.exp(-age_days / 90.0)
        weighted_similarity = numeric_similarity * trust * (1.0 - 0.5 * risk) * recency
        scored.append(
            (
                weighted_similarity,
                {
                    **row,
                    "retrieval": {
                        "numeric_similarity": round(numeric_similarity, 6),
                        "trust_factor": round(trust, 6),
                        "risk_factor": round(1.0 - 0.5 * risk, 6),
                        "recency_factor": round(recency, 6),
                    },
                },
            )
        )
    scored.sort(key=lambda item: (item[0], str(item[1].get("record_hash", ""))), reverse=True)
    return scored[:k]
