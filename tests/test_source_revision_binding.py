from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import api
from app.persistence import ReceiptLedger
from app.source_identity import deployed_source_sha

SOURCE_SHA = "a" * 40


def test_unconfigured_revision_file_preserves_legacy_identity(monkeypatch) -> None:
    monkeypatch.delenv("GDW_SOURCE_REVISION_FILE", raising=False)
    monkeypatch.delenv("GDW_SOURCE_SHA", raising=False)
    assert deployed_source_sha() == "UNAVAILABLE"

    monkeypatch.setenv("GDW_SOURCE_SHA", "legacy-source-identity")
    assert deployed_source_sha() == "legacy-source-identity"


def test_generated_revision_file_binds_exact_sha(tmp_path: Path, monkeypatch) -> None:
    revision_file = tmp_path / "source-revision.txt"
    revision_file.write_bytes(SOURCE_SHA.encode("ascii") + b"\n")
    monkeypatch.setenv("GDW_SOURCE_REVISION_FILE", str(revision_file))
    monkeypatch.setenv("GDW_SOURCE_SHA", SOURCE_SHA)
    assert deployed_source_sha() == SOURCE_SHA

    monkeypatch.setenv("GDW_SOURCE_SHA", "UNAVAILABLE")
    assert deployed_source_sha() == SOURCE_SHA


def test_missing_revision_file_fails_closed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GDW_SOURCE_REVISION_FILE", str(tmp_path / "missing.txt"))
    with pytest.raises(RuntimeError, match="GDW_SOURCE_REVISION_FILE is unavailable"):
        deployed_source_sha()


def test_blank_revision_file_setting_fails_closed(monkeypatch) -> None:
    monkeypatch.setenv("GDW_SOURCE_REVISION_FILE", " ")
    with pytest.raises(RuntimeError, match="must name a revision file"):
        deployed_source_sha()


def test_relative_revision_file_setting_fails_closed(monkeypatch) -> None:
    monkeypatch.setenv("GDW_SOURCE_REVISION_FILE", "source-revision.txt")
    with pytest.raises(RuntimeError, match="must be an absolute path"):
        deployed_source_sha()


@pytest.mark.parametrize(
    "content",
    [
        b"",
        SOURCE_SHA.encode("ascii"),
        SOURCE_SHA.upper().encode("ascii") + b"\n",
        SOURCE_SHA.encode("ascii") + b"\r\n",
        b"a" * 39 + b"\n",
        SOURCE_SHA.encode("ascii") + b"\nextra",
        b"g" * 40 + b"\n",
        b"\xff" * 40 + b"\n",
    ],
)
def test_malformed_revision_file_fails_closed(
    tmp_path: Path, monkeypatch, content: bytes
) -> None:
    revision_file = tmp_path / "source-revision.txt"
    revision_file.write_bytes(content)
    monkeypatch.setenv("GDW_SOURCE_REVISION_FILE", str(revision_file))
    with pytest.raises(RuntimeError, match="one lowercase 40-hex SHA and LF"):
        deployed_source_sha()


def test_environment_and_file_conflict_fails_closed(tmp_path: Path, monkeypatch) -> None:
    revision_file = tmp_path / "source-revision.txt"
    revision_file.write_bytes(SOURCE_SHA.encode("ascii") + b"\n")
    monkeypatch.setenv("GDW_SOURCE_REVISION_FILE", str(revision_file))
    monkeypatch.setenv("GDW_SOURCE_SHA", "b" * 40)
    with pytest.raises(RuntimeError, match="GDW_SOURCE_SHA conflicts"):
        deployed_source_sha()


def test_build_info_and_runtime_identity_use_revision_file(
    tmp_path: Path, monkeypatch
) -> None:
    revision_file = tmp_path / "source-revision.txt"
    revision_file.write_bytes(SOURCE_SHA.encode("ascii") + b"\n")
    monkeypatch.setenv("GDW_SOURCE_REVISION_FILE", str(revision_file))
    monkeypatch.delenv("GDW_SOURCE_SHA", raising=False)
    monkeypatch.delenv("GDW_REQUIRE_AUTH", raising=False)
    monkeypatch.setattr(
        api,
        "ledger",
        ReceiptLedger(
            tmp_path / "receipts.jsonl",
            signing_required=False,
            use_configured_signing_key=False,
        ),
    )
    monkeypatch.setattr(api.exemplar_store, "path", tmp_path / "exemplars.jsonl")

    with TestClient(api.app) as client:
        assert client.get("/build-info").json()["source_sha"] == SOURCE_SHA
        assert api._runtime_identity()["source_sha"] == SOURCE_SHA


def test_startup_rejects_missing_configured_revision_file(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("GDW_SOURCE_REVISION_FILE", str(tmp_path / "missing.txt"))
    with pytest.raises(RuntimeError, match="GDW_SOURCE_REVISION_FILE is unavailable"):
        with TestClient(api.app):
            pass


def test_valid_revision_drift_fails_readiness_identity_and_receipt_actions(
    tmp_path: Path, monkeypatch
) -> None:
    revision_file = tmp_path / "source-revision.txt"
    revision_file.write_bytes(SOURCE_SHA.encode("ascii") + b"\n")
    monkeypatch.setenv("GDW_SOURCE_REVISION_FILE", str(revision_file))
    monkeypatch.delenv("GDW_SOURCE_SHA", raising=False)
    monkeypatch.delenv("GDW_REQUIRE_AUTH", raising=False)
    monkeypatch.setattr(
        api,
        "ledger",
        ReceiptLedger(
            tmp_path / "receipts.jsonl",
            signing_required=False,
            use_configured_signing_key=False,
        ),
    )
    monkeypatch.setattr(api.exemplar_store, "path", tmp_path / "exemplars.jsonl")

    with TestClient(api.app) as client:
        assert client.get("/readyz").status_code == 200
        assert client.get("/build-info").json()["source_sha"] == SOURCE_SHA
        records_before = api.ledger.audit().records
        revision_file.write_bytes(b"b" * 40 + b"\n")

        for route in ("/readyz", "/build-info", "/v1/gdw/ledger"):
            response = client.get(route)
            assert response.status_code == 503
            assert response.json() == {"detail": "source revision binding invalid"}
        step = client.post(
            "/v1/gdw/step",
            json={
                "session_id": "revision-drift",
                "proposal_id": "cannot-write",
                "objective": "prove the drift gate blocks receipt actions",
                "confidence": 0.9,
                "receipt_present": True,
                "scheduler_mode": "balanced",
            },
        )
        assert step.status_code == 503
        assert api.ledger.audit().records == records_before
        with pytest.raises(RuntimeError, match="content changed after startup"):
            api._runtime_identity()


def test_revision_path_change_fails_readiness(tmp_path: Path, monkeypatch) -> None:
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_bytes(SOURCE_SHA.encode("ascii") + b"\n")
    second.write_bytes(SOURCE_SHA.encode("ascii") + b"\n")
    monkeypatch.setenv("GDW_SOURCE_REVISION_FILE", str(first))
    monkeypatch.delenv("GDW_SOURCE_SHA", raising=False)
    monkeypatch.delenv("GDW_REQUIRE_AUTH", raising=False)
    monkeypatch.setattr(
        api,
        "ledger",
        ReceiptLedger(
            tmp_path / "receipts.jsonl",
            signing_required=False,
            use_configured_signing_key=False,
        ),
    )
    monkeypatch.setattr(api.exemplar_store, "path", tmp_path / "exemplars.jsonl")

    with TestClient(api.app) as client:
        assert client.get("/readyz").status_code == 200
        monkeypatch.setenv("GDW_SOURCE_REVISION_FILE", str(second))
        assert client.get("/readyz").status_code == 503


def test_unconfigured_file_keeps_legacy_build_info_behavior(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("GDW_SOURCE_REVISION_FILE", raising=False)
    monkeypatch.setenv("GDW_SOURCE_SHA", "c" * 40)
    monkeypatch.delenv("GDW_REQUIRE_AUTH", raising=False)
    monkeypatch.setattr(
        api,
        "ledger",
        ReceiptLedger(
            tmp_path / "receipts.jsonl",
            signing_required=False,
            use_configured_signing_key=False,
        ),
    )
    monkeypatch.setattr(api.exemplar_store, "path", tmp_path / "exemplars.jsonl")

    with TestClient(api.app) as client:
        assert client.get("/build-info").json()["source_sha"] == "c" * 40
        monkeypatch.setenv("GDW_SOURCE_SHA", "d" * 40)
        assert client.get("/build-info").json()["source_sha"] == "d" * 40
        assert client.get("/readyz").status_code == 200
