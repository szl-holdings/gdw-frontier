from __future__ import annotations

from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app.exemplar_store import ExemplarStore


def test_signed_exemplar_memory_has_independent_continuity_state(
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

    assert store.verify() == (False, 0)
    activation_hash = store.ensure_signed_head(
        {"service": "gdw", "source_sha": "test-source"}
    )
    assert activation_hash is not None
    assert store.verify() == (True, 1)
    assert store.signing_state_path.exists()
    assert store.signing_state_path.name == "incident-exemplars-signing-state.json"

    record_hash, created = store.append_once(
        {"snapshot": {"snapshot_id": "incident-1"}},
        idempotency_key="incident-1",
    )
    assert created is True
    assert len(record_hash) == 64
    assert store.verify() == (True, 2)
    assert len(store.load()) == 1
