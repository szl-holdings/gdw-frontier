from __future__ import annotations

from typing import Any

from .diagnostics import DiagnosticSnapshot


def decode_incident(snapshot: DiagnosticSnapshot) -> dict[str, Any]:
    if snapshot.missing_receipt_rate > 0.0:
        return {
            "incident_type": "receipt_integrity_failure",
            "severity": "critical",
            "reversibility": "blocking",
            "human_approval_required": True,
        }
    if not snapshot.weight_validation_passed:
        return {
            "incident_type": "weight_integrity_failure",
            "severity": "critical",
            "reversibility": "blocking",
            "human_approval_required": False,
        }
    if snapshot.error_rate > 0.05:
        return {
            "incident_type": "error_rate_spike",
            "severity": "high",
            "reversibility": "partial",
            "human_approval_required": True,
        }
    if snapshot.p95_latency_ms > 1_500.0:
        return {
            "incident_type": "latency_regression",
            "severity": "medium",
            "reversibility": "reversible",
            "human_approval_required": False,
        }
    if (snapshot.prometheus_series_count or 0) > 500_000:
        return {
            "incident_type": "cardinality_pressure",
            "severity": "medium",
            "reversibility": "reversible",
            "human_approval_required": True,
        }
    return {
        "incident_type": "healthy",
        "severity": "low",
        "reversibility": "none",
        "human_approval_required": False,
    }
