from __future__ import annotations

import json
import multiprocessing
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app.persistence import ReceiptLedger


def _append_records(path: str, worker: int, count: int) -> None:
    ledger = ReceiptLedger(
        Path(path),
        signing_required=False,
        use_configured_signing_key=False,
    )
    for index in range(count):
        ledger.append({"worker": worker, "index": index})


def _signing_key(path: Path) -> Path:
    private_key = ec.generate_private_key(ec.SECP256R1())
    path.write_bytes(
        private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return path


def test_receipt_chain_verifies_and_detects_tampering(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    ledger = ReceiptLedger(
        path,
        signing_required=False,
        use_configured_signing_key=False,
    )
    first_hash = ledger.append({"decision": "allow"})
    second_hash = ledger.append({"decision": "review"})

    assert first_hash != second_hash
    assert ledger.verify() == (True, 2)

    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    records[0]["body"]["decision"] = "withhold"
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
    assert ledger.verify()[0] is False


def test_receipt_chain_is_safe_across_worker_processes(tmp_path: Path) -> None:
    path = tmp_path / "multiprocess-ledger.jsonl"
    context = multiprocessing.get_context("spawn")
    processes = [
        context.Process(target=_append_records, args=(str(path), worker, 5))
        for worker in range(4)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0
    assert ReceiptLedger(
        path,
        signing_required=False,
        use_configured_signing_key=False,
    ).verify() == (True, 20)


def test_signed_head_anchors_legacy_history_and_detects_downgrade(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    legacy = ReceiptLedger(
        path,
        signing_required=False,
        use_configured_signing_key=False,
    )
    legacy.append({"decision": "allow"})

    key_path = _signing_key(tmp_path / "signing-key.pem")
    signed = ReceiptLedger(
        path,
        signing_key_path=key_path,
        signing_required=True,
        allow_unsigned_migration=True,
    )
    signed.ensure_signed_head({"source_sha": "a" * 40})
    audit = signed.audit()
    assert audit.valid is True
    assert audit.signature_status == "SIGNED"
    assert audit.legacy_unsigned_records == 1
    assert audit.signed_records == 1
    assert audit.signing_key_id.startswith("sha256:")

    records = path.read_text(encoding="utf-8").splitlines()
    path.write_text(records[0] + "\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="lost the signed transition"):
        signed.ensure_signed_head({"source_sha": "a" * 40})
    assert signed.audit().valid is False
    assert signed.audit().signature_status == "INVALID"
    signed.signing_state_path.unlink()
    restarted = ReceiptLedger(
        path,
        signing_key_path=key_path,
        signing_required=True,
        allow_unsigned_migration=False,
    )
    with pytest.raises(RuntimeError, match="initialization or legacy migration"):
        restarted.ensure_signed_head({"source_sha": "a" * 40})


def test_legacy_unsigned_migration_requires_explicit_one_time_approval(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ledger.jsonl"
    ReceiptLedger(
        path,
        signing_required=False,
        use_configured_signing_key=False,
    ).append({"decision": "allow"})
    key_path = _signing_key(tmp_path / "signing-key.pem")
    signed = ReceiptLedger(path, signing_key_path=key_path, signing_required=True)

    with pytest.raises(RuntimeError, match="GDW_ALLOW_UNSIGNED_LEDGER_MIGRATION=1"):
        signed.ensure_signed_head({"source_sha": "a" * 40})


def test_signed_only_ledger_deletion_cannot_be_silently_reinitialized(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    key_path = _signing_key(tmp_path / "signing-key.pem")
    signed = ReceiptLedger(
        path,
        signing_key_path=key_path,
        signing_required=True,
        allow_unsigned_migration=True,
    )
    signed.ensure_signed_head({"source_sha": "a" * 40})
    state_path = signed.signing_state_path
    assert state_path.exists()

    path.unlink()
    with pytest.raises(RuntimeError, match="lost the signed transition"):
        signed.ensure_signed_head({"source_sha": "a" * 40})
    assert not path.exists()


def test_empty_signed_initialization_is_explicit_and_cannot_be_replayed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ledger.jsonl"
    key_path = _signing_key(tmp_path / "signing-key.pem")
    unapproved = ReceiptLedger(
        path,
        signing_key_path=key_path,
        signing_required=True,
    )
    with pytest.raises(RuntimeError, match="initialization or legacy migration"):
        unapproved.ensure_signed_head({"source_sha": "a" * 40})

    approved = ReceiptLedger(
        path,
        signing_key_path=key_path,
        signing_required=True,
        allow_unsigned_migration=True,
    )
    approved.ensure_signed_head({"source_sha": "a" * 40})
    state_path = approved.signing_state_path
    assert path.exists()
    assert state_path.exists()

    path.unlink()
    state_path.unlink()
    restarted = ReceiptLedger(
        path,
        signing_key_path=key_path,
        signing_required=True,
        allow_unsigned_migration=False,
    )
    with pytest.raises(RuntimeError, match="initialization or legacy migration"):
        restarted.ensure_signed_head({"source_sha": "a" * 40})
    assert not path.exists()
    assert not state_path.exists()


def test_every_audit_rejects_removed_anchor_signature(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    key_path = _signing_key(tmp_path / "signing-key.pem")
    signed = ReceiptLedger(
        path,
        signing_key_path=key_path,
        signing_required=True,
        allow_unsigned_migration=True,
    )
    signed.ensure_signed_head({"source_sha": "a" * 40})
    signed.append({"decision": "allow"})

    records = [
        json.loads(raw_line)
        for raw_line in path.read_text(encoding="utf-8").splitlines()
    ]
    records[0].pop("signature")
    records[0].pop("signature_algorithm")
    records[0].pop("key_id")
    path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
    )

    assert signed.audit().valid is False
    assert signed.audit().signature_status == "INVALID"
    with pytest.raises(RuntimeError, match="valid signed head"):
        signed.head_proof()
    with pytest.raises(RuntimeError, match="valid signed head"):
        signed.create_backup(tmp_path / "backups", {"source_sha": "a" * 40})


def test_optional_signing_creates_continuity_state_and_backup(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    key_path = _signing_key(tmp_path / "signing-key.pem")
    optional = ReceiptLedger(
        path,
        signing_key_path=key_path,
        signing_required=False,
        allow_unsigned_migration=True,
    )
    optional.ensure_signed_head({"source_sha": "a" * 40})
    optional.append({"decision": "allow"})

    assert optional.audit().signature_status == "SIGNED"
    assert optional.signing_state_path.exists()
    manifest = optional.create_backup(
        tmp_path / "backups",
        {"source_sha": "a" * 40},
    )
    assert manifest["payload"]["ledger"]["records"] == 2


@pytest.mark.parametrize("malformed_state", ["42\n", "\"invalid\"\n"])
def test_malformed_continuity_state_returns_invalid_audit(
    tmp_path: Path,
    malformed_state: str,
) -> None:
    path = tmp_path / "ledger.jsonl"
    key_path = _signing_key(tmp_path / "signing-key.pem")
    signed = ReceiptLedger(
        path,
        signing_key_path=key_path,
        signing_required=True,
        allow_unsigned_migration=True,
    )
    signed.ensure_signed_head({"source_sha": "a" * 40})
    signed.signing_state_path.write_text(malformed_state, encoding="utf-8")

    audit = signed.audit()
    assert audit.valid is False
    assert audit.signature_status == "INVALID"


def test_signed_backup_restores_exact_head_and_rejects_tampering(tmp_path: Path) -> None:
    key_path = _signing_key(tmp_path / "signing-key.pem")
    ledger = ReceiptLedger(
        tmp_path / "ledger.jsonl",
        signing_key_path=key_path,
        signing_required=True,
        allow_unsigned_migration=True,
    )
    ledger.ensure_signed_head({"source_sha": "b" * 40})
    ledger.append({"decision": "review"})
    original = ledger.audit()

    manifest = ledger.create_backup(
        tmp_path / "backups",
        {"source_sha": "b" * 40, "image_digest": "sha256:" + "c" * 64},
    )
    backup_path = Path(manifest["path"])
    proof = ledger.verify_and_restore_backup(
        backup_path,
        tmp_path / "restore" / "receipts.jsonl",
    )
    assert proof["status"] == "VERIFIED"
    assert proof["records"] == original.records
    assert proof["head_hash"] == original.head_hash
    assert (tmp_path / "restore" / "receipt-signing-state.json").exists()

    backup_state = backup_path / "receipt-signing-state.json"
    state_bytes = backup_state.read_bytes()
    backup_state.write_bytes(state_bytes + b"{}")
    with pytest.raises(ValueError, match="signing-state digest mismatch"):
        ledger.verify_and_restore_backup(
            backup_path,
            tmp_path / "tampered-state" / "receipts.jsonl",
        )
    backup_state.write_bytes(state_bytes)

    backup_ledger = backup_path / "receipts.jsonl"
    backup_ledger.write_bytes(backup_ledger.read_bytes() + b"{}\n")
    with pytest.raises(ValueError, match="digest mismatch"):
        ledger.verify_and_restore_backup(
            backup_path,
            tmp_path / "tampered" / "receipts.jsonl",
        )
