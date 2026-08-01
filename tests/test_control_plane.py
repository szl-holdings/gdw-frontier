from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from app import api
from app.benchmarks import benchmark_decoder, quant_cal_eval
from app.diagnostics import DiagnosticSnapshot
from app.exemplar_store import ExemplarStore
from app.quant_core import release_score


def _snapshot(**overrides):
    snapshot = {
        "snapshot_id": "incident-37",
        "p50_latency_ms": 500.0,
        "p95_latency_ms": 2_000.0,
        "p99_latency_ms": 2_500.0,
        "error_rate": 0.08,
        "inflight_requests": 25,
        "missing_receipt_rate": 0.0,
        "prometheus_series_count": 600_000,
        "log_volume_per_min": 1_000,
        "weight_validation_passed": True,
    }
    snapshot.update(overrides)
    return snapshot


def _action(action_type: str = "quarantine_route"):
    return {
        "type": action_type,
        "target": "/v1/gdw/step",
        "rationale": "modeled test action",
        "human_approval_required": True,
    }


def test_quant_core_is_monotonic_for_worse_latency() -> None:
    healthy = release_score(200.0, 0.0, 0.0, True, 0.0)
    slower = release_score(2_000.0, 0.0, 0.0, True, 0.0)
    assert slower < healthy


def test_empty_exemplar_memory_gets_a_signed_genesis_record(
    tmp_path: Path,
    monkeypatch,
) -> None:
    private_key = ec.generate_private_key(ec.SECP256R1())
    key_path = tmp_path / "signing-key.pem"
    key_path.write_bytes(
        private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    monkeypatch.setenv("GDW_RECEIPT_SIGNING_KEY_FILE", str(key_path))
    monkeypatch.setenv("GDW_RECEIPT_SIGNING_REQUIRED", "1")
    monkeypatch.setenv("GDW_ALLOW_UNSIGNED_LEDGER_MIGRATION", "1")

    store = ExemplarStore(tmp_path / "incident-exemplars.jsonl")
    identity = {"source_sha": "a" * 40, "component": "incident_memory"}
    first_head = store.ensure_signed_head(identity)
    second_head = store.ensure_signed_head(identity)

    assert first_head == second_head
    assert store.verify() == (True, 1)
    assert store.load() == []


def test_calibration_decode_and_simulation_surface(tmp_path: Path) -> None:
    api.ledger.path = tmp_path / "receipts.jsonl"
    api.exemplar_store.path = tmp_path / "exemplars.jsonl"
    with TestClient(api.app) as client:
        calibration = client.post("/v1/gdw/calibrate", json={"snapshot": _snapshot()})
        assert calibration.status_code == 200
        action_types = {action["type"] for action in calibration.json()["actions"]}
        assert action_types == {"raise_alert", "quarantine_route", "increase_scrape_interval"}
        assert calibration.json()["status"] == "MODELED"

        decoded = client.post("/v1/gdw/decode", json={"snapshot": _snapshot()})
        assert decoded.json()["incident_type"] == "error_rate_spike"

        simulated = client.post(
            "/v1/gdw/simulate",
            json={"snapshot": _snapshot(), "action": _action()},
        )
        assert simulated.status_code == 200
        assert simulated.json()["projected_snapshot"]["error_rate"] == 0.04
        assert simulated.json()["projection_interval"]["conservative"]["error_rate"] == pytest.approx(0.064)
        assert simulated.json()["effect_model"]["evidence_status"] == "MODELED"
        assert simulated.json()["projected_release_score"] > calibration.json()["release_score"]


def test_exemplar_memory_is_governed_and_searchable(tmp_path: Path) -> None:
    api.ledger.path = tmp_path / "receipts.jsonl"
    api.exemplar_store.path = tmp_path / "exemplars.jsonl"
    with TestClient(api.app) as client:
        rejected = client.post(
            "/v1/gdw/exemplars",
            json={"snapshot": _snapshot(), "receipt_present": False},
        )
        assert rejected.json()["decision"] == "withhold"
        assert rejected.json()["record_hash"] is None
        assert len(rejected.json()["receipt_hash"]) == 64

        recorded = client.post(
            "/v1/gdw/exemplars",
            json={
                "snapshot": _snapshot(),
                "intervention": _action(),
                "outcome": {"result": "recovered"},
            },
        )
        assert recorded.json()["decision"] == "allow"
        assert recorded.json()["created"] is True
        assert len(recorded.json()["record_hash"]) == 64

        duplicate = client.post(
            "/v1/gdw/exemplars",
            json={
                "snapshot": _snapshot(),
                "intervention": _action(),
                "outcome": {"result": "recovered"},
            },
        )
        assert duplicate.json()["record_hash"] == recorded.json()["record_hash"]
        assert duplicate.json()["created"] is False

        query = {**_snapshot(snapshot_id="query-37"), "k": 3}
        found = client.get("/v1/gdw/exemplars/search", params=query)
        assert found.status_code == 200
        assert found.json()["matches"][0]["snapshot_id"] == "incident-37"
        assert found.json()["matches"][0]["outcome"] == {"result": "recovered"}


def test_threshold_changes_require_approval_and_are_receipted(tmp_path: Path) -> None:
    api.ledger.path = tmp_path / "receipts.jsonl"
    api.exemplar_store.path = tmp_path / "exemplars.jsonl"
    with TestClient(api.app) as client:
        response = client.post(
            "/v1/gdw/quant/calibrate",
            json={
                "current_threshold": 0.8,
                "predicted_confidence": 0.9,
                "observed_success": 0.5,
            },
        )
        body = response.json()
        assert body["policy_decision"] == "review"
        assert body["proposed_threshold"] == 0.76
        assert len(body["receipt_hash"]) == 64
        assert body["policy_revision"] == "gdw-x/2026-07-29"


def test_modeled_benchmarks_are_explicit() -> None:
    decoder = benchmark_decoder()
    quant = quant_cal_eval([(0.9, 1.0), (0.8, 0.0)])
    assert decoder["accuracy"] == 1.0
    assert decoder["status"] == "MODELED"
    assert quant["sample_count"] == 2
    assert quant["status"] == "MODELED"
    assert DiagnosticSnapshot.model_validate(_snapshot()).snapshot_id == "incident-37"


def test_risk_control_refuses_insufficient_evidence_and_emits_held_out_threshold(
    tmp_path: Path,
) -> None:
    api.ledger.path = tmp_path / "receipts.jsonl"
    api.exemplar_store.path = tmp_path / "exemplars.jsonl"
    with TestClient(api.app) as client:
        unavailable = client.post(
            "/v1/gdw/quant/risk-control",
            json={"nonconformity_scores": [0.1, 0.2], "minimum_samples": 3},
        )
        assert unavailable.json()["evidence_status"] == "UNAVAILABLE"
        assert unavailable.json()["threshold"] is None

        measured = client.post(
            "/v1/gdw/quant/risk-control",
            json={
                "nonconformity_scores": [0.05, 0.1, 0.15, 0.2, 0.25],
                "alpha": 0.2,
                "minimum_samples": 5,
            },
        )
        assert measured.json()["evidence_status"] == "MEASURED"
        assert measured.json()["threshold"] == 0.25
