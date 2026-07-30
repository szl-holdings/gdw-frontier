from __future__ import annotations

import json
import multiprocessing
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app.persistence import ReceiptLedger


def _append_records(path: str, worker: int, count: int) -> None:
    ledger = ReceiptLedger(Path(path))
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
    ledger = ReceiptLedger(path)
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
    assert ReceiptLedger(path).verify() == (True, 20)


def test_signed_head_anchors_legacy_history_and_detects_downgrade(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    legacy = ReceiptLedger(
        path,
        signing_required=False,
        use_configured_signing_key=False,
    )
    legacy.append({"decision": "allow"})

    key_path = _signing_key(tmp_path / "signing-key.pem")
    signed = ReceiptLedger(path, signing_key_path=key_path, signing_required=True)
    signed.ensure_signed_head({"source_sha": "a" * 40})
    audit = signed.audit()
    assert audit.valid is True
    assert audit.signature_status == "SIGNED"
    assert audit.legacy_unsigned_records == 1
    assert audit.signed_records == 1
    assert audit.signing_key_id.startswith("sha256:")

    records = path.read_text(encoding="utf-8").splitlines()
    path.write_text(records[0] + "\n", encoding="utf-8")
    assert signed.audit().valid is False
    assert signed.audit().signature_status == "INVALID"
    with pytest.raises(RuntimeError, match="cannot anchor an invalid receipt ledger"):
        signed.ensure_signed_head({"source_sha": "a" * 40})


def test_signed_transition_rejects_deleted_signed_only_ledger(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    key_path = _signing_key(tmp_path / "signing-key.pem")
    signed = ReceiptLedger(path, signing_key_path=key_path, signing_required=True)
    signed.ensure_signed_head({"source_sha": "a" * 40})

    assert signed.signing_transition_path.is_file()
    path.unlink()

    restarted = ReceiptLedger(path, signing_key_path=key_path, signing_required=True)
    with pytest.raises(RuntimeError, match="cannot anchor an invalid receipt ledger"):
        restarted.ensure_signed_head({"source_sha": "a" * 40})


def test_signed_backup_restores_exact_head_and_rejects_tampering(tmp_path: Path) -> None:
    key_path = _signing_key(tmp_path / "signing-key.pem")
    ledger = ReceiptLedger(
        tmp_path / "ledger.jsonl",
        signing_key_path=key_path,
        signing_required=True,
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

    backup_ledger = backup_path / "receipts.jsonl"
    backup_ledger.write_bytes(backup_ledger.read_bytes() + b"{}\n")
    with pytest.raises(ValueError, match="digest mismatch"):
        ledger.verify_and_restore_backup(
            backup_path,
            tmp_path / "tampered" / "receipts.jsonl",
        )
