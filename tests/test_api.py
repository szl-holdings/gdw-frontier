from __future__ import annotations

import os
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


def test_required_auth_protects_operator_endpoints_and_leaves_probes_public(
    tmp_path: Path,
    monkeypatch,
) -> None:
    token = "test-token-that-is-at-least-32-characters"
    monkeypatch.setenv("GDW_REQUIRE_AUTH", "true")
    monkeypatch.setenv("GDW_API_TOKEN", token)
    monkeypatch.delenv("GDW_API_TOKEN_FILE", raising=False)
    api.ledger.path = tmp_path / "receipts.jsonl"

    with TestClient(api.app) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/readyz").status_code == 200
        assert client.get("/build-info").status_code == 200
        assert client.get("/metrics").status_code == 200

        unauthorized = client.get("/v1/gdw/ledger")
        assert unauthorized.status_code == 401
        assert unauthorized.headers["www-authenticate"] == "Bearer"

        authorized = client.get(
            "/v1/gdw/ledger",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert authorized.status_code == 200
        assert authorized.json()["valid"] is True


def test_build_info_reports_exact_runtime_identity(monkeypatch) -> None:
    monkeypatch.delenv("GDW_REQUIRE_AUTH", raising=False)
    monkeypatch.setenv("GDW_SOURCE_SHA", "a" * 40)
    monkeypatch.setenv("GDW_IMAGE_DIGEST", "sha256:" + "b" * 64)
    monkeypatch.setenv("GDW_DEPLOYMENT_MODE", "SINGLE_NODE_LIVE")

    with TestClient(api.app) as client:
        response = client.get("/build-info")
        assert response.status_code == 200
        assert response.json() == {
            "service": "gdw",
            "version": "0.1.0",
            "source_sha": "a" * 40,
            "image_digest": "sha256:" + "b" * 64,
            "deployment_mode": "SINGLE_NODE_LIVE",
            "model_status": "MODELED",
        }


def test_auth_enabled_rejects_weak_runtime_token(monkeypatch) -> None:
    monkeypatch.setenv("GDW_REQUIRE_AUTH", "true")
    monkeypatch.setenv("GDW_API_TOKEN", "too-short")
    monkeypatch.delenv("GDW_API_TOKEN_FILE", raising=False)

    try:
        with TestClient(api.app):
            pass
    except RuntimeError as error:
        assert "at least 32 characters" in str(error)
    else:
        raise AssertionError("weak production token unexpectedly started the service")

    os.environ.pop("GDW_REQUIRE_AUTH", None)
    os.environ.pop("GDW_API_TOKEN", None)


def test_auth_token_file_takes_precedence_over_environment(
    tmp_path: Path,
    monkeypatch,
) -> None:
    file_token = "file-token-that-is-at-least-32-characters"
    token_file = tmp_path / "gdw_api_token"
    token_file.write_text(file_token + "\n", encoding="utf-8")
    monkeypatch.setenv("GDW_REQUIRE_AUTH", "true")
    monkeypatch.setenv("GDW_API_TOKEN", "wrong-environment-token-that-is-still-long")
    monkeypatch.setenv("GDW_API_TOKEN_FILE", str(token_file))

    with TestClient(api.app) as client:
        denied = client.get(
            "/v1/gdw/ledger",
            headers={"Authorization": "Bearer wrong-environment-token-that-is-still-long"},
        )
        assert denied.status_code == 401
        allowed = client.get(
            "/v1/gdw/ledger",
            headers={"Authorization": f"Bearer {file_token}"},
        )
        assert allowed.status_code == 200
