from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import api
from app.persistence import ReceiptLedger
from app.runtime import governed_worker_count
from ci.receipt_proof import verify_signed_receipt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


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
    api.exemplar_store.path = tmp_path / "exemplars.jsonl"
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
    api.exemplar_store.path = tmp_path / "exemplars.jsonl"
    with TestClient(api.app) as client:
        response = client.post(
            "/v1/gdw/step",
            json=_payload(confidence=1.0, receipt_present=False),
        )
        assert response.status_code == 200
        assert response.json()["decision"] == "withhold"
        assert len(response.json()["receipt_hash"]) == 64
        assert api.ledger.verify() == (True, 1)


def test_metrics_do_not_expose_identity_values_or_labels(tmp_path: Path) -> None:
    api.ledger.path = tmp_path / "receipts.jsonl"
    api.exemplar_store.path = tmp_path / "exemplars.jsonl"
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
    api.exemplar_store.path = tmp_path / "exemplars.jsonl"
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
        root = client.get("/")
        assert root.status_code == 200
        assert root.json() == {
            "service": "gdw",
            "status": "ready",
            "model_status": "MODELED",
            "operator_api": "bearer-protected",
            "public_probes": [
                "/healthz",
                "/readyz",
                "/build-info",
                "/calibration",
                "/master-suite",
            ],
        }
        assert client.head("/").status_code == 200
        assert client.get("/healthz").status_code == 200
        assert client.get("/readyz").status_code == 200
        assert client.get("/build-info").status_code == 200
        assert client.get("/metrics").status_code == 200
        assert client.get("/calibration").status_code == 200
        assert client.get("/master-suite").status_code == 200

        unauthorized = client.get("/v1/gdw/ledger")
        assert unauthorized.status_code == 401
        assert unauthorized.headers["www-authenticate"] == "Bearer"

        authorized = client.get(
            "/v1/gdw/ledger",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert authorized.status_code == 200
        assert authorized.json()["valid"] is True


def test_build_info_reports_exact_runtime_identity(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("GDW_REQUIRE_AUTH", raising=False)
    monkeypatch.setenv("GDW_SOURCE_SHA", "a" * 40)
    monkeypatch.setenv("GDW_IMAGE_DIGEST", "sha256:" + "b" * 64)
    monkeypatch.setenv("GDW_DEPLOYMENT_MODE", "SINGLE_NODE_LIVE")
    monkeypatch.setattr(
        api,
        "ledger",
        ReceiptLedger(
            tmp_path / "receipts.jsonl",
            signing_required=False,
            use_configured_signing_key=False,
        ),
    )

    with TestClient(api.app) as client:
        response = client.get("/build-info")
        assert response.status_code == 200
        assert response.json() == {
            "service": "gdw",
            "version": "0.3.0",
            "source_sha": "a" * 40,
            "image_digest": "sha256:" + "b" * 64,
            "deployment_mode": "SINGLE_NODE_LIVE",
            "receipt_signing": "UNAVAILABLE",
            "receipt_signing_key_id": None,
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


def test_asgi_lifespan_rejects_multiple_workers(monkeypatch) -> None:
    monkeypatch.setenv("GDW_WORKERS", "2")

    with pytest.raises(RuntimeError, match="GDW_WORKERS must be 1"):
        with TestClient(api.app):
            pass


def test_runtime_rejects_alternate_worker_count_sources(monkeypatch) -> None:
    monkeypatch.setenv("GDW_WORKERS", "1")
    monkeypatch.setenv("WEB_CONCURRENCY", "2")
    with pytest.raises(RuntimeError, match="WEB_CONCURRENCY must be 1"):
        governed_worker_count()

    monkeypatch.delenv("WEB_CONCURRENCY")
    monkeypatch.setattr(
        sys,
        "argv",
        ["uvicorn", "app.api:app", "--workers", "2"],
    )
    with pytest.raises(RuntimeError, match="uvicorn --workers must be 1"):
        api._validate_runtime_configuration()


def test_runtime_rejects_uvicorn_environment_bypasses(monkeypatch) -> None:
    monkeypatch.setenv("GDW_WORKERS", "1")
    monkeypatch.setenv("UVICORN_WORKERS", "2")
    with pytest.raises(RuntimeError, match="UVICORN_WORKERS must be 1"):
        governed_worker_count()

    monkeypatch.setenv("UVICORN_WORKERS", "1")
    monkeypatch.setenv("UVICORN_LIFESPAN", "off")
    with pytest.raises(RuntimeError, match="UVICORN_LIFESPAN=off is unsupported"):
        governed_worker_count()


def test_programmatic_lifespan_disablement_serves_only_unavailable() -> None:
    api._GOVERNED_RUNTIME_READY.clear()
    client = TestClient(api.app)
    try:
        response = client.get("/healthz")
    finally:
        client.close()

    assert response.status_code == 503
    assert response.json() == {"detail": "governed runtime startup incomplete"}


@pytest.mark.parametrize(
    ("ledger_valid", "memory_valid"),
    [(False, True), (True, False)],
)
def test_lifespan_rejects_invalid_startup_audits(
    tmp_path: Path,
    monkeypatch,
    ledger_valid: bool,
    memory_valid: bool,
) -> None:
    api.ledger.path = tmp_path / "receipts.jsonl"
    api.exemplar_store.path = tmp_path / "exemplars.jsonl"
    monkeypatch.setattr(
        api.ledger,
        "audit",
        lambda: SimpleNamespace(
            valid=ledger_valid,
            records=0,
            signature_status="UNAVAILABLE",
            signing_key_id=None,
        ),
    )
    monkeypatch.setattr(api.exemplar_store, "verify", lambda: (memory_valid, 0))

    with pytest.raises(RuntimeError, match="governed runtime startup audit failed"):
        with TestClient(api.app):
            pass

    assert not api._GOVERNED_RUNTIME_READY.is_set()


def test_direct_asgi_import_rejects_disabled_lifespan(
    tmp_path: Path,
) -> None:
    environment = os.environ.copy()
    environment["GDW_DATA_DIR"] = str(tmp_path)
    environment["GDW_WORKERS"] = "1"
    environment.pop("GDWX_MASTER_SUITE_SIGNING_KEY_FILE", None)
    environment.pop("GDWX_MASTER_SUITE_SIGNING_KEY", None)

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import app.api",
            "uvicorn",
            "app.api:app",
            "--workers",
            "1",
            "--lifespan",
            "off",
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "uvicorn --lifespan off is unsupported" in result.stderr


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


def test_signed_receipt_and_backup_surfaces_are_cryptographically_bound(
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
    signed_ledger = ReceiptLedger(
        tmp_path / "receipts.jsonl",
        signing_key_path=key_path,
        signing_required=True,
        allow_unsigned_migration=True,
    )
    monkeypatch.setattr(api, "ledger", signed_ledger)
    monkeypatch.setenv("GDW_BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setenv("GDW_SOURCE_SHA", "d" * 40)
    monkeypatch.setenv("GDW_IMAGE_DIGEST", "sha256:" + "e" * 64)

    with TestClient(api.app) as client:
        step = client.post("/v1/gdw/step", json=_payload())
        assert step.status_code == 200

        status = client.get("/v1/gdw/ledger").json()
        assert status["valid"] is True
        assert status["signature_status"] == "SIGNED"
        assert status["signed_records"] == 2
        assert status["legacy_unsigned_records"] == 0

        receipt = client.get("/v1/gdw/receipt")
        assert receipt.status_code == 200
        assert receipt.json()["record"]["receipt_hash"] == step.json()["receipt_hash"]
        assert receipt.json()["record"]["body"]["runtime_identity"]["source_sha"] == "d" * 40
        assert verify_signed_receipt(receipt.json())["valid"] is True

        backup = client.post("/v1/gdw/backup")
        assert backup.status_code == 200
        assert backup.json()["status"] == "VERIFIED"
        assert backup.json()["head_hash"] == step.json()["receipt_hash"]
