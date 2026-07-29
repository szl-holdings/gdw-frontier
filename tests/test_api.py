from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app import api


def _payload(**overrides):
    payload = {
        "session_id": "sensitive-session-value",
        "proposal_id": "proposal-1",
        "objective": "test the modeled policy",
        "confidence": 0.90,
        "receipt_present": True,
        "scheduler_mode": "balanced",
    }
    payload.update(overrides)
    return payload


def test_health_and_step_are_truth_labeled(tmp_path: Path) -> None:
    api.ledger.path = tmp_path / "receipts.jsonl"
    with TestClient(api.app) as client:
        health = client.get("/healthz")
        assert health.status_code == 200
        assert health.json() == {"status": "ready", "service": "gdw", "model_status": "MODELED"}

        response = client.post("/v1/gdw/step", json=_payload())
        assert response.status_code == 200
        body = response.json()
        assert body["decision"] == "allow"
        assert body["receipt_status"] == "valid"
        assert body["status"] == "MODELED"
        assert len(body["receipt_hash"]) == 64
        assert body["request_id"] == response.headers["x-request-id"]
        assert body["session_ref"] != "sensitive-session-value"


def test_missing_receipt_fails_closed(tmp_path: Path) -> None:
    api.ledger.path = tmp_path / "receipts.jsonl"
    with TestClient(api.app) as client:
        response = client.post(
            "/v1/gdw/step",
            json=_payload(confidence=1.0, receipt_present=False),
        )
        assert response.status_code == 200
        assert response.json()["decision"] == "withhold"
        assert response.json()["receipt_hash"] is None


def test_metrics_do_not_expose_identity_values_or_labels(tmp_path: Path) -> None:
    api.ledger.path = tmp_path / "receipts.jsonl"
    with TestClient(api.app) as client:
        client.post("/v1/gdw/step", json=_payload())
        metrics = client.get("/metrics").text
        assert "gdw_request_total" in metrics
        assert "sensitive-session-value" not in metrics
        assert "proposal-1" not in metrics
        assert "request_id=" not in metrics
        assert "session_id=" not in metrics
        metric_samples = [line.split(" # ", 1)[0] for line in metrics.splitlines() if not line.startswith("#")]
        assert all('trace_id="' not in sample for sample in metric_samples)
        assert ' # {trace_id="' in metrics


def test_validation_rejects_unknown_fields(tmp_path: Path) -> None:
    api.ledger.path = tmp_path / "receipts.jsonl"
    with TestClient(api.app) as client:
        response = client.post("/v1/gdw/step", json=_payload(user_id="unsafe"))
        assert response.status_code == 422
