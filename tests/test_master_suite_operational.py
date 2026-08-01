from __future__ import annotations

from datetime import datetime, timedelta, timezone

from gdw_x.control_plane.master_suite_service import MasterSuiteService
from gdw_x.domain.dasc import DASCLocalizer
from gdw_x.domain.master_suite import (
    DeploymentMode,
    FormulaObservation,
    OrderIntent,
    OutcomeObservation,
    StrategyMutation,
)


def _evidence_window() -> tuple[FormulaObservation, list[FormulaObservation]]:
    base = datetime.now(timezone.utc) - timedelta(minutes=30)
    reference: list[FormulaObservation] = []
    for index in range(20):
        predicted = 100.0 + index * 0.01
        reference.append(
            FormulaObservation(
                formula_id="frontier-alpha",
                formula_version="1.0.0",
                timestamp=base + timedelta(minutes=index),
                predicted=predicted,
                realized=predicted + (0.05 if index % 2 else -0.05),
                raw_confidence=0.88,
                regime_id="stable",
                features={
                    "volatility": 0.20 + index * 0.001,
                    "liquidity": 0.80 - index * 0.001,
                },
                feature_schema_hash="schema-v1",
                data_snapshot_hash=f"snapshot-{index}",
                latency_ms=4.0,
            )
        )
    observation = FormulaObservation(
        formula_id="frontier-alpha",
        formula_version="1.0.0",
        timestamp=datetime.now(timezone.utc),
        predicted=100.25,
        realized=100.30,
        raw_confidence=0.88,
        regime_id="stable",
        features={"volatility": 0.21, "liquidity": 0.79},
        feature_schema_hash="schema-v1",
        data_snapshot_hash="snapshot-current",
        latency_ms=4.5,
    )
    return observation, reference


def test_stable_window_does_not_false_quarantine() -> None:
    observation, reference = _evidence_window()

    result = DASCLocalizer().localize(observation, reference)

    assert result.reference_size == 20
    assert result.evidence_quality == 1.0
    assert result.drift.maximum < 0.80
    assert result.abstain is False


def test_signed_modeled_lifecycle_is_replayable_and_fail_closed(tmp_path) -> None:
    service = MasterSuiteService(
        data_root=tmp_path,
        signing_key="test-governance-key",
    )
    observation, reference = _evidence_window()
    parent = service.dashboard_state()["active_runtime_hash"]
    mutation = StrategyMutation(
        strategy_id="frontier-alpha-strategy",
        formula_id="frontier-alpha",
        from_version="1.0.0",
        candidate_version="1.0.1",
        parameter_diff={"signal_weight": 0.02},
        evidence_hashes=["evidence-replay-001"],
        requested_mode=DeploymentMode.PAPER,
        parent_runtime_hash=parent,
        requestor_identity="strategy-lab",
        reversibility_plan="Restore version 1.0.0 and its parameter hash.",
    )
    submitted = service.submit_mutation(
        mutation,
        observation,
        reference,
        correlation_id="test-lifecycle",
    )
    proposal_hash = submitted["proposal_hash"]

    assert service.simulate(
        proposal_hash,
        correlation_id="test-lifecycle",
    )["passed"] is True
    assert service.decide(
        proposal_hash,
        correlation_id="test-lifecycle",
    ).decision.value == "accept"
    manifest = service.issue_manifest(
        proposal_hash,
        expires_in_seconds=900,
        correlation_id="test-lifecycle",
    )
    permitted = service.evaluate_risk(
        OrderIntent(
            actor_identity="paper-runtime",
            symbol="TEST",
            size=10.0,
            price=100.0,
            price_deviation=0.01,
            requested_mode=DeploymentMode.PAPER,
        ),
        manifest.manifest_hash,
        correlation_id="test-lifecycle",
    )
    assert permitted.allow is True
    assert permitted.broker_submission is False

    live_request = service.evaluate_risk(
        OrderIntent(
            actor_identity="paper-runtime",
            symbol="TEST",
            size=10.0,
            price=100.0,
            requested_mode=DeploymentMode.LIVE,
        ),
        manifest.manifest_hash,
        correlation_id="test-live-rejection",
    )
    assert live_request.allow is False
    assert "live_execution_authority_unavailable" in live_request.reason_codes

    ack = service.acknowledge(
        manifest.manifest_hash,
        "paper-runtime-1",
        correlation_id="test-lifecycle",
    )
    assert ack.status == "applied"
    service.record_outcome(
        OutcomeObservation(
            proposal_hash=proposal_hash,
            forecast_loss=0.04,
            calibration_error=0.03,
            latency_p95_ms=8.2,
            error_rate=0.0,
            risk_events=0,
            realized_utility=0.71,
        ),
        correlation_id="test-lifecycle",
    )

    replayed = MasterSuiteService(
        data_root=tmp_path,
        signing_key="test-governance-key",
    ).dashboard_state()
    assert replayed["journal"]["integrity_status"] == "VERIFIED"
    assert replayed["chain_counts"] == {
        "mutations": 1,
        "simulations": 1,
        "decisions": 1,
        "manifests": 1,
        "acks": 1,
        "outcomes": 1,
    }
