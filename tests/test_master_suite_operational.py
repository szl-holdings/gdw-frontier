from __future__ import annotations

import multiprocessing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from gdw_x.control_plane.master_suite_service import (
    MasterSuiteError,
    MasterSuiteService,
)
from gdw_x.domain.dasc import DASCLocalizer
from gdw_x.domain.master_suite import (
    DeploymentMode,
    FormulaObservation,
    OrderIntent,
    OutcomeObservation,
    StrategyMutation,
)
from gdw_x.memory.lake_journal import LakeJournal


def _append_journal_events(root: str, worker: int, count: int) -> None:
    journal = LakeJournal(Path(root))
    for index in range(count):
        journal.append(
            event_type="ConcurrentAppend",
            aggregate_type="test",
            aggregate_id=f"{worker}-{index}",
            payload={"worker": worker, "index": index},
            correlation_id=f"worker-{worker}",
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


def test_bronze_journal_serializes_concurrent_processes(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    processes = [
        context.Process(
            target=_append_journal_events,
            args=(str(tmp_path), worker, 5),
        )
        for worker in range(4)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=30)
        assert process.exitcode == 0

    events = LakeJournal(tmp_path).read_events()
    assert [event["sequence"] for event in events] == list(range(1, 21))
    assert [event["previous_event_hash"] for event in events] == [
        "GENESIS",
        *[event["event_hash"] for event in events[:-1]],
    ]


def test_master_suite_defaults_to_the_configured_writable_data_dir(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("GDW_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GDWX_MASTER_SUITE_DATA_ROOT", "")

    service = MasterSuiteService(signing_key="test-governance-key")

    assert service._journal.root == tmp_path / "master_suite"
    assert (tmp_path / "master_suite" / ".lake-journal.lock").is_file()


def test_master_suite_prefers_the_file_backed_signing_key(
    tmp_path: Path,
    monkeypatch,
) -> None:
    key_path = tmp_path / "master-suite-signing-key"
    key_path.write_text("file-backed-governance-key\n", encoding="utf-8")
    monkeypatch.setenv("GDWX_MASTER_SUITE_SIGNING_KEY_FILE", str(key_path))
    monkeypatch.setenv("GDWX_MASTER_SUITE_SIGNING_KEY", "environment-fallback")

    service = MasterSuiteService(data_root=tmp_path / "service")

    assert service._signing_key == "file-backed-governance-key"


def test_strategy_mutation_hash_excludes_generated_identity_and_timestamp() -> None:
    values = {
        "strategy_id": "frontier-alpha-strategy",
        "formula_id": "frontier-alpha",
        "from_version": "1.0.0",
        "candidate_version": "1.0.1",
        "parameter_diff": {"signal_weight": 0.02},
        "evidence_hashes": ["evidence-replay-001"],
        "requested_mode": DeploymentMode.PAPER,
        "parent_runtime_hash": "runtime-parent",
        "requestor_identity": "strategy-lab",
        "reversibility_plan": "Restore the prior version.",
    }
    first = StrategyMutation(**values)
    second = StrategyMutation(**values)

    assert first.mutation_id != second.mutation_id
    assert first.proposal_hash() == second.proposal_hash()


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
    retry_mutation = StrategyMutation.model_validate(
        mutation.model_dump(
            mode="python",
            exclude={"mutation_id", "created_at"},
        )
    )
    retried = service.submit_mutation(
        retry_mutation,
        observation,
        reference,
        correlation_id="test-lifecycle-retry",
    )
    assert retried["proposal_hash"] == proposal_hash
    assert retried["idempotent"] is True

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
    before_ack = service.evaluate_risk(
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
    assert before_ack.allow is False
    assert "runtime_ack_missing" in before_ack.reason_codes
    assert "active_runtime_hash_mismatch" in before_ack.reason_codes

    original_runtime_hash = service._active_runtime_hash
    service._active_runtime_hash = "f" * 64
    rejected_ack = service.acknowledge(
        manifest.manifest_hash,
        "rejecting-runtime",
        correlation_id="test-rejected-ack",
    )
    assert rejected_ack.status == "rejected"
    blocked_outcome = OutcomeObservation(
        proposal_hash=proposal_hash,
        forecast_loss=0.04,
        calibration_error=0.03,
        latency_p95_ms=8.2,
        error_rate=0.0,
        risk_events=0,
        realized_utility=0.71,
    )
    with pytest.raises(MasterSuiteError) as outcome_error:
        service.record_outcome(
            blocked_outcome,
            correlation_id="test-rejected-ack",
        )
    assert outcome_error.value.code == "applied_runtime_ack_required"
    service._active_runtime_hash = original_runtime_hash

    ack = service.acknowledge(
        manifest.manifest_hash,
        "paper-runtime-1",
        correlation_id="test-lifecycle",
    )
    assert ack.status == "applied"

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
    service._acks = {
        "rejected-ack": {
            "manifest_hash": manifest.manifest_hash,
            "status": "rejected",
            "deployment_mode": DeploymentMode.LIVE.value,
        },
        **service._acks,
    }
    recorded = service.record_outcome(
        blocked_outcome,
        correlation_id="test-lifecycle",
    )
    assert recorded["gold_exemplar"]["deployment_mode"] == DeploymentMode.PAPER.value
    assert recorded["gold_exemplar"]["rollback_flag"] is False

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
        "acks": 2,
        "outcomes": 1,
    }
