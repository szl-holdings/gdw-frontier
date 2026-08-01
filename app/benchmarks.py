from __future__ import annotations

from typing import Any

from .anomaly_decoder import decode_incident
from .diagnostics import DiagnosticSnapshot
from .quant_core import release_score
from .simulator import simulate_intervention


def _snapshot(snapshot_id: str, **overrides: Any) -> DiagnosticSnapshot:
    values: dict[str, Any] = {
        "snapshot_id": snapshot_id,
        "p50_latency_ms": 100.0,
        "p95_latency_ms": 200.0,
        "p99_latency_ms": 300.0,
        "error_rate": 0.001,
        "inflight_requests": 5,
        "missing_receipt_rate": 0.0,
        "prometheus_series_count": 100,
        "log_volume_per_min": 50,
        "weight_validation_passed": True,
    }
    values.update(overrides)
    return DiagnosticSnapshot.model_validate(values)


DECODER_CASES = (
    ("healthy", _snapshot("benchmark-healthy")),
    (
        "receipt_integrity_failure",
        _snapshot("benchmark-receipts", missing_receipt_rate=0.01),
    ),
    (
        "weight_integrity_failure",
        _snapshot("benchmark-weights", weight_validation_passed=False),
    ),
    ("error_rate_spike", _snapshot("benchmark-errors", error_rate=0.10)),
    ("latency_regression", _snapshot("benchmark-latency", p95_latency_ms=2_000.0)),
    (
        "cardinality_pressure",
        _snapshot("benchmark-cardinality", prometheus_series_count=600_000),
    ),
)


def benchmark_decoder() -> dict[str, Any]:
    cases = []
    correct = 0
    for expected, snapshot in DECODER_CASES:
        actual = str(decode_incident(snapshot)["incident_type"])
        passed = actual == expected
        correct += int(passed)
        cases.append(
            {
                "snapshot_id": snapshot.snapshot_id,
                "expected": expected,
                "actual": actual,
                "passed": passed,
            }
        )
    return {
        "benchmark": "OpCalEval.decoder.v1",
        "benchmark_kind": "contract_fixture",
        "case_count": len(cases),
        "correct": correct,
        "accuracy": correct / len(cases),
        "cases": cases,
        "status": "MODELED",
        "claim_boundary": "Deterministic rule coverage only; no external holdout performance claim.",
    }


def _score(snapshot: DiagnosticSnapshot) -> float:
    return release_score(
        p95_latency_ms=snapshot.p95_latency_ms,
        error_rate=snapshot.error_rate,
        missing_receipt_rate=snapshot.missing_receipt_rate,
        weight_validation_passed=snapshot.weight_validation_passed,
        cardinality_risk=min(1.0, float(snapshot.prometheus_series_count or 0) / 500_000.0),
    )


def op_cal_eval(snapshot: DiagnosticSnapshot, actions: list[dict[str, Any]]) -> dict[str, Any]:
    baseline = _score(snapshot)
    candidates = []
    for action in actions:
        simulation = simulate_intervention(snapshot, action)
        projected = simulation["projected_snapshot"]
        projected_score = _score(projected)
        conservative_score = _score(simulation["projection_interval"]["conservative"])
        optimistic_score = _score(simulation["projection_interval"]["optimistic"])
        candidates.append(
            {
                "action": action,
                "projected_release_score": projected_score,
                "projected_delta": projected_score - baseline,
                "projected_score_interval": {
                    "conservative": conservative_score,
                    "central": projected_score,
                    "optimistic": optimistic_score,
                },
                "conservative_delta": conservative_score - baseline,
                "applied": simulation["applied"],
                "assumptions": simulation["assumptions"],
                "effect_model": simulation["effect_model"],
            }
        )
    candidates.sort(
        key=lambda item: (item["conservative_delta"], str(item["action"].get("type"))),
        reverse=True,
    )
    return {
        "benchmark": "OpCalEval.intervention.v1",
        "benchmark_kind": "counterfactual_model",
        "baseline_release_score": baseline,
        "ranked_candidates": candidates,
        "status": "MODELED",
        "claim_boundary": "Ranks explicit modeled effects; target-environment causal validity is UNVERIFIED.",
    }


def quant_cal_eval(observations: list[tuple[float, float]]) -> dict[str, Any]:
    if not observations:
        raise ValueError("at least one calibration observation is required")
    absolute_errors = [abs(predicted - observed) for predicted, observed in observations]
    squared_errors = [(predicted - observed) ** 2 for predicted, observed in observations]
    return {
        "benchmark": "QuantCalEval.v1",
        "benchmark_kind": "supplied_observations",
        "sample_count": len(observations),
        "mean_absolute_calibration_error": sum(absolute_errors) / len(absolute_errors),
        "brier_score": sum(squared_errors) / len(squared_errors),
        "status": "MODELED",
        "claim_boundary": "Descriptive calibration metrics only; no distribution-shift guarantee.",
    }
