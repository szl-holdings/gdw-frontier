from __future__ import annotations

from typing import Any

from .diagnostics import DiagnosticSnapshot
from .incident_memory import find_similar
from .quant_core import release_score_breakdown


def calibrate(
    snapshot: DiagnosticSnapshot,
    exemplars: list[dict[str, Any]],
    k: int = 3,
) -> dict[str, Any]:
    similar = find_similar(snapshot, exemplars, k=k)
    cardinality_risk = min(1.0, float(snapshot.prometheus_series_count or 0) / 500_000.0)
    score = release_score_breakdown(
        p95_latency_ms=snapshot.p95_latency_ms,
        error_rate=snapshot.error_rate,
        missing_receipt_rate=snapshot.missing_receipt_rate,
        weight_validation_passed=snapshot.weight_validation_passed,
        cardinality_risk=cardinality_risk,
    )

    actions: list[dict[str, Any]] = []
    if snapshot.p95_latency_ms > 1_500.0:
        actions.append(
            {
                "type": "raise_alert",
                "target": "latency",
                "rationale": "p95 latency exceeds the modeled 1500 ms threshold",
                "human_approval_required": False,
            }
        )
    if snapshot.error_rate > 0.05:
        actions.append(
            {
                "type": "quarantine_route",
                "target": "/v1/gdw/step",
                "rationale": "error rate exceeds the modeled 5 percent threshold",
                "human_approval_required": True,
            }
        )
    if (snapshot.prometheus_series_count or 0) > 500_000:
        actions.append(
            {
                "type": "increase_scrape_interval",
                "target": "prometheus",
                "value": "10s",
                "rationale": "series count exceeds the modeled cardinality budget",
                "human_approval_required": True,
            }
        )
    if not snapshot.weight_validation_passed:
        actions.append(
            {
                "type": "block_weights",
                "target": "weights",
                "rationale": "artifact weight validation failed",
                "human_approval_required": False,
            }
        )

    return {
        "release_score": score["score"],
        "score_breakdown": score,
        "similar_incidents": [
            {
                "snapshot_id": str(row["snapshot"]["snapshot_id"]),
                "similarity": round(similarity, 6),
                "record_hash": row.get("record_hash"),
            }
            for similarity, row in similar
        ],
        "actions": actions,
    }
