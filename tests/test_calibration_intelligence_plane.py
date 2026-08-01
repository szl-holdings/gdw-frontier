from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app import api
from gdw_x.apps.calibration_api import router as calibration_router
from gdw_x.control_plane.calibration_service import CalibrationService
from gdw_x.domain.contracts import (
    CalibrationEpisode,
    CalibrationProposal,
    Decision,
    DeploymentMode,
    DriftVector,
)


def _proposal(**overrides) -> CalibrationProposal:
    values = {
        "proposal_id": "proposal-frontier-1",
        "created_at": datetime(2026, 7, 29, tzinfo=timezone.utc),
        "formula_id": "formula-a",
        "from_version": "1.0.0",
        "candidate_version": "1.1.0",
        "proposed_parameters": {"alpha": 0.2},
        "proposed_confidence_transform": {"kind": "multiplicative", "factor": 0.9},
        "evidence_hashes": ["a" * 64],
        "drift": DriftVector(input_drift=0.2, calibration_drift=0.1),
        "expected_utility_delta": 0.05,
        "uncertainty": 0.15,
        "reversible": True,
        "requested_mode": DeploymentMode.PAPER,
        "parent_runtime_hash": "0" * 64,
        "proposer_identity": "calibration-plane",
    }
    values.update(overrides)
    return CalibrationProposal.model_validate(values)


def test_proposal_and_receipt_hashes_are_replay_stable(tmp_path: Path) -> None:
    service = CalibrationService(tmp_path, signing_key=b"test-key", signing_key_source="environment")
    first = service.create_proposal(_proposal())
    second = service.create_proposal(_proposal())
    assert first["proposal_hash"] == second["proposal_hash"]
    assert first["receipt_hash"] == second["receipt_hash"]
    assert first["created"] is True
    assert second["created"] is False


def test_paper_flow_is_governed_authenticated_and_idempotent(tmp_path: Path) -> None:
    service = CalibrationService(tmp_path, signing_key=b"test-key", signing_key_source="environment")
    created = service.create_proposal(_proposal())
    proposal_hash = created["proposal_hash"]
    assert set(service.scenarios) == {
        "abrupt_regime_shift",
        "gradual_drift",
        "recurring_regime",
        "telemetry_loss",
    }
    simulation = service.simulate(proposal_hash, ["recurring_regime"])
    assert simulation["state"] == "simulated"
    assert len(simulation["results"]) == 1
    decision = service.decide(proposal_hash)
    assert decision.decision == Decision.ACCEPT
    assert decision.approved_mode == DeploymentMode.PAPER
    manifest = service.issue_manifest(proposal_hash)
    assert service.authenticator.verify_manifest(manifest)
    first_ack = service.apply_manifest(manifest.canonical_hash())
    second_ack = service.apply_manifest(manifest.canonical_hash())
    assert first_ack.canonical_hash() == second_ack.canonical_hash()
    assert first_ack.active_mode == DeploymentMode.PAPER
    assert service.status()["runtime"]["active_mode"] == "paper"

    replacement_manifest = service.issue_manifest(proposal_hash)
    replacement_manifest_hash = replacement_manifest.canonical_hash()
    assert replacement_manifest_hash != manifest.canonical_hash()
    noop_ack = service.apply_manifest(replacement_manifest_hash)
    assert noop_ack.status == "applied"
    assert noop_ack.active_runtime_hash == first_ack.active_runtime_hash
    assert service.runtime.applied_manifest_hash == replacement_manifest_hash

    try:
        service.apply_manifest(manifest.canonical_hash())
    except PermissionError as exc:
        assert "superseded by the current runtime binding" in str(exc)
    else:
        raise AssertionError("superseded manifest acknowledgement must fail closed")
    assert service.runtime.applied_manifest_hash == replacement_manifest_hash

    outcome = service.record_outcome(
        proposal_hash=proposal_hash,
        manifest_hash=replacement_manifest_hash,
        runtime_instance=noop_ack.runtime_instance,
        observed_runtime_hash=noop_ack.active_runtime_hash,
        regime_id="recurring",
        parameter_hash="b" * 64,
        observed_utility=0.03,
        rollback_status="not_required",
    )
    assert outcome["created"] is True
    matches = service.search_exemplars(
        formula_id="formula-a",
        regime_id="recurring",
        proposal_hash=proposal_hash,
        k=3,
    )
    assert matches[0]["proposal_hash"] == proposal_hash
    assert matches[0]["runtime_binding_status"] == "BOUND"

    legacy_episode = CalibrationEpisode(
        episode_id="legacy-frontier-episode",
        recorded_at=datetime(2026, 7, 29, tzinfo=timezone.utc),
        formula_id="formula-a",
        formula_version="1.0.0",
        regime_id="recurring",
        parameter_hash="c" * 64,
        drift=service.proposals[proposal_hash].drift,
        proposal_hash=proposal_hash,
        decision=Decision.ACCEPT,
        deployment_mode=DeploymentMode.PAPER,
        observed_utility=0.02,
        predicted_utility=0.03,
        rollback_status="not_required",
        evidence_hashes=["a" * 64],
        uncertainty=0.15,
    )
    service.lake.store(legacy_episode)
    legacy_matches = service.search_exemplars(
        formula_id="formula-a",
        regime_id="recurring",
        proposal_hash=proposal_hash,
        k=3,
    )
    legacy_match = next(
        item
        for item in legacy_matches
        if item["episode_id"] == legacy_episode.episode_id
    )
    assert legacy_match["manifest_hash"] is None
    assert legacy_match["runtime_binding_status"] == "LEGACY_UNBOUND"

    successor = _proposal(
        proposal_id="proposal-frontier-2",
        candidate_version="1.2.0",
        proposed_parameters={"alpha": 0.21},
        parent_runtime_hash=service.runtime.active_runtime_hash,
    )
    successor_hash = service.create_proposal(successor)["proposal_hash"]
    service.simulate(successor_hash, ["recurring_regime"])
    service.decide(successor_hash)
    successor_manifest = service.issue_manifest(successor_hash)
    service.apply_manifest(successor_manifest.canonical_hash())
    try:
        service.record_outcome(
            proposal_hash=proposal_hash,
            manifest_hash=manifest.canonical_hash(),
            runtime_instance=first_ack.runtime_instance,
            observed_runtime_hash=first_ack.active_runtime_hash,
            regime_id="recurring",
            parameter_hash="b" * 64,
            observed_utility=0.03,
            rollback_status="not_required",
        )
    except PermissionError as error:
        assert "current applied runtime" in str(error)
    else:
        raise AssertionError("superseded runtime outcome was accepted")


def test_protected_parameters_and_market_authority_fail_closed(tmp_path: Path) -> None:
    service = CalibrationService(tmp_path, signing_key=b"test-key", signing_key_source="environment")
    protected = _proposal(
        proposal_id="protected",
        proposed_parameters={"credit_limit": 1_000_000.0},
    )
    protected_hash = service.create_proposal(protected)["proposal_hash"]
    service.simulate(protected_hash, ["recurring_regime"])
    protected_decision = service.decide(protected_hash)
    assert protected_decision.decision == Decision.QUARANTINE

    live = _proposal(proposal_id="live", requested_mode=DeploymentMode.LIVE)
    live_hash = service.create_proposal(live)["proposal_hash"]
    service.simulate(live_hash, ["recurring_regime"])
    live_decision = service.decide(live_hash)
    assert live_decision.decision == Decision.REVIEW
    assert "mode_requires_independent_approval" in live_decision.reason_codes


def test_calibration_api_exposes_status_and_paper_flow(tmp_path: Path) -> None:
    calibration_router.service = CalibrationService(
        tmp_path,
        signing_key=b"api-test-key",
        signing_key_source="environment",
    )
    with TestClient(api.app) as client:
        status = client.get("/v1/calibration/status")
        assert status.status_code == 200
        assert status.json()["unavailable_modes"] == ["canary", "live"]

        proposal = _proposal(proposal_id="api-proposal")
        created = client.post(
            "/v1/calibration/proposals",
            json=proposal.model_dump(mode="json"),
        )
        assert created.status_code == 200
        proposal_hash = created.json()["proposal_hash"]

        simulated = client.post(
            "/v1/calibration/simulate",
            json={"proposal_hash": proposal_hash, "scenario_ids": ["recurring_regime"]},
        )
        assert simulated.status_code == 200

        decision = client.post(
            "/v1/calibration/decisions",
            json={"proposal_hash": proposal_hash},
        )
        assert decision.json()["decision"] == "accept"

        manifest = client.post(
            "/v1/calibration/manifests",
            json={"proposal_hash": proposal_hash, "ttl_seconds": 300},
        )
        assert manifest.status_code == 200

        blocked_outcome = client.post(
            "/v1/calibration/outcomes",
            json={
                "proposal_hash": proposal_hash,
                "manifest_hash": manifest.json()["manifest_hash"],
                "runtime_instance": calibration_router.service.runtime.runtime_instance,
                "observed_runtime_hash": manifest.json()["manifest"]["target_runtime_hash"],
                "regime_id": "recurring",
                "parameter_hash": "b" * 64,
                "observed_utility": 0.03,
                "rollback_status": "pending",
            },
        )
        assert blocked_outcome.status_code == 403
        assert "applied runtime acknowledgement" in blocked_outcome.json()["detail"]

        applied = client.post(
            "/v1/calibration/apply",
            json={"manifest_hash": manifest.json()["manifest_hash"]},
        )
        assert applied.json()["active_mode"] == "paper"
