from __future__ import annotations

from typing import Any

from .diagnostics import DiagnosticSnapshot


def simulate_intervention(snapshot: DiagnosticSnapshot, action: dict[str, Any]) -> dict[str, Any]:
    conservative = snapshot.model_copy(deep=True)
    projected = snapshot.model_copy(deep=True)
    optimistic = snapshot.model_copy(deep=True)
    action_type = str(action.get("type", ""))
    applied = True
    assumptions: list[str] = []

    if action_type == "increase_scrape_interval":
        conservative.prometheus_series_count = int((conservative.prometheus_series_count or 0) * 0.95)
        projected.prometheus_series_count = int((projected.prometheus_series_count or 0) * 0.85)
        optimistic.prometheus_series_count = int((optimistic.prometheus_series_count or 0) * 0.75)
        assumptions.append("MODELED 5 to 25 percent series-count reduction")
    elif action_type == "adjust_worker_count":
        conservative.p50_latency_ms *= 0.97
        conservative.p95_latency_ms *= 0.97
        conservative.p99_latency_ms *= 0.97
        projected.p50_latency_ms *= 0.9
        projected.p95_latency_ms *= 0.9
        projected.p99_latency_ms *= 0.9
        optimistic.p50_latency_ms *= 0.85
        optimistic.p95_latency_ms *= 0.85
        optimistic.p99_latency_ms *= 0.85
        assumptions.append("MODELED 3 to 15 percent latency reduction")
    elif action_type == "quarantine_route":
        conservative.error_rate *= 0.8
        projected.error_rate *= 0.5
        optimistic.error_rate *= 0.4
        assumptions.append("MODELED 20 to 60 percent error-rate reduction")
    elif action_type in {"raise_alert", "block_weights", "record_exemplar", "adjust_threshold"}:
        assumptions.append("control action does not directly alter diagnostic telemetry")
    else:
        applied = False
        assumptions.append("unknown action type; snapshot left unchanged")

    return {
        "projected_snapshot": projected,
        "projection_interval": {
            "conservative": conservative,
            "central": projected,
            "optimistic": optimistic,
        },
        "applied": applied,
        "assumptions": assumptions,
        "effect_model": {
            "model_id": "gdw-x.heuristic-effects.v1",
            "evidence_status": "MODELED",
            "requires_target_environment_validation": True,
        },
    }
