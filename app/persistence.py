from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, BinaryIO

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

WINDOWS_LOCK_TIMEOUT_SECONDS = 30.0
WINDOWS_LOCK_RETRY_SECONDS = 0.01
SIGNATURE_ALGORITHM = "ECDSA_P256_SHA256"
TRUE_VALUES = frozenset({"1", "true", "yes"})


def canonical_json(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


@dataclass(frozen=True)
class LedgerAudit:
    valid: bool
    records: int
    head_hash: str | None
    signed_records: int
    legacy_unsigned_records: int
    signature_status: str
    signature_algorithm: str | None
    signing_key_id: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ReceiptLedger:
    """Append-only hash chain with an optional file-backed P-256 signing root."""

    def __init__(
        self,
        path: Path | None = None,
        *,
        signing_key_path: Path | None = None,
        signing_state_path: Path | None = None,
        signing_required: bool | None = None,
        allow_unsigned_migration: bool | None = None,
        use_configured_signing_key: bool = True,
    ) -> None:
        data_dir = Path(os.getenv("GDW_DATA_DIR", "data"))
        self.path = path or data_dir / "receipts.jsonl"
        configured_key = (
            os.getenv("GDW_RECEIPT_SIGNING_KEY_FILE", "").strip()
            if use_configured_signing_key
            else ""
        )
        self.signing_key_path = signing_key_path or (Path(configured_key) if configured_key else None)
        self._signing_state_path = signing_state_path
        self.signing_required = (
            signing_required
            if signing_required is not None
            else os.getenv("GDW_RECEIPT_SIGNING_REQUIRED", "").strip().lower() in TRUE_VALUES
        )
        self.allow_unsigned_migration = (
            allow_unsigned_migration
            if allow_unsigned_migration is not None
            else os.getenv("GDW_ALLOW_UNSIGNED_LEDGER_MIGRATION", "").strip().lower()
            in TRUE_VALUES
        )
        self._lock = Lock()
        self._private_key: ec.EllipticCurvePrivateKey | None = None

    @property
    def signing_state_path(self) -> Path:
        return self._signing_state_path or self.path.with_name(
            "receipt-signing-state.json"
        )

    def append(self, body: dict[str, Any]) -> str:
        with self._lock:
            with self._process_lock():
                return self._append_locked(body)

    def append_once(self, body: dict[str, Any], idempotency_key: str) -> tuple[str, bool]:
        if not idempotency_key:
            raise ValueError("idempotency_key cannot be empty")
        idempotent_body = {**body, "idempotency_key": idempotency_key}
        with self._lock:
            with self._process_lock():
                if self.path.exists():
                    for raw_line in self.path.read_text(encoding="utf-8").splitlines():
                        record = json.loads(raw_line)
                        if record.get("body", {}).get("idempotency_key") == idempotency_key:
                            return str(record["receipt_hash"]), False
                return self._append_locked(idempotent_body), True

    def verify(self) -> tuple[bool, int]:
        audit = self.audit()
        return audit.valid, audit.records

    def status(self) -> tuple[bool, int, str | None]:
        audit = self.audit()
        return audit.valid, audit.records, audit.head_hash

    def audit(self) -> LedgerAudit:
        with self._lock:
            with self._process_lock():
                return self._audit_with_signing_state_locked()

    def verified_records(self) -> list[dict[str, Any]]:
        """Return the exact parsed ledger snapshot that passed verification."""
        with self._lock:
            with self._process_lock():
                try:
                    records = self._read_records()
                except (
                    json.JSONDecodeError,
                    TypeError,
                    UnicodeDecodeError,
                    ValueError,
                ) as error:
                    raise RuntimeError(
                        "receipt ledger failed integrity or signature verification"
                    ) from error
                audit = self._audit_signing_state_locked(
                    self._audit_records_locked(records),
                    records=records,
                )
                if not audit.valid:
                    raise RuntimeError(
                        "receipt ledger failed integrity or signature verification"
                    )
                return records

    def validate_configuration(self) -> None:
        if self.signing_required and self.signing_key_path is None:
            raise RuntimeError(
                "GDW_RECEIPT_SIGNING_KEY_FILE is required when GDW_RECEIPT_SIGNING_REQUIRED is enabled"
            )
        if self.signing_key_path is not None:
            self._load_private_key()

    def signing_identity(self) -> tuple[str | None, str | None]:
        if self.signing_key_path is None:
            return None, None
        return SIGNATURE_ALGORITHM, self._key_id()

    def ensure_signed_head(self, runtime_identity: dict[str, Any]) -> str | None:
        if self.signing_key_path is None:
            return None
        activation = {
            "event": "runtime_activation",
            "model_status": "MODELED",
            "runtime_identity": runtime_identity,
        }
        with self._lock:
            with self._process_lock():
                signing_state = self._read_signing_state()
                audit = self._audit_locked()
                if signing_state is not None:
                    if not audit.valid or audit.signature_status != "SIGNED":
                        raise RuntimeError(
                            "receipt ledger lost the signed transition recorded by its continuity state"
                        )
                    self._validate_signing_state(signing_state)
                elif audit.valid and audit.signature_status == "SIGNED":
                    signing_state = self._derive_signing_state()
                    self._write_signing_state(signing_state)
                else:
                    migration_audit = self._audit_locked(allow_unsigned_head=True)
                    if not migration_audit.valid:
                        raise RuntimeError("cannot anchor an invalid receipt ledger")
                    if not self.allow_unsigned_migration:
                        raise RuntimeError(
                            "signed receipt initialization or legacy migration requires "
                            "GDW_ALLOW_UNSIGNED_LEDGER_MIGRATION=1 for one startup"
                        )
                    receipt_hash = self._append_locked(activation)
                    signing_state = self._signing_state_for_boundary(
                        legacy_records=migration_audit.records,
                        legacy_head_hash=migration_audit.head_hash,
                        activation_receipt_hash=receipt_hash,
                    )
                    self._write_signing_state(signing_state)
                    return receipt_hash
                if self.path.exists():
                    for raw_line in reversed(
                        self.path.read_text(encoding="utf-8").splitlines()
                    ):
                        record = json.loads(raw_line)
                        if (
                            record.get("signature")
                            and record.get("body") == activation
                            and record.get("key_id") == self._key_id()
                        ):
                            return str(record["receipt_hash"])
                return self._append_locked(activation)

    def head_proof(self) -> dict[str, Any]:
        with self._lock:
            with self._process_lock():
                audit = self._audit_with_signing_state_locked()
                if not audit.valid or audit.signature_status != "SIGNED":
                    raise RuntimeError("the receipt ledger has no valid signed head")
                record = self._last_record()
                if record is None or not record.get("signature"):
                    raise RuntimeError("the receipt ledger head is not signed")
                return {
                    "valid": True,
                    "record": record,
                    "public_key_pem": self._public_key_pem(),
                    "audit": audit.to_dict(),
                }

    def create_backup(self, backup_dir: Path, context: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            with self._process_lock():
                audit = self._audit_with_signing_state_locked()
                if not audit.valid or audit.signature_status != "SIGNED":
                    raise RuntimeError("refusing to back up a ledger without a valid signed head")
                signing_state = self._read_signing_state()
                if signing_state is None:
                    raise RuntimeError("refusing to back up a ledger without signing continuity state")
                self._validate_signing_state(signing_state)
                ledger_bytes = self.path.read_bytes()
                signing_state_bytes = self.signing_state_path.read_bytes()
                created_at = datetime.now(timezone.utc).isoformat()
                backup_id = (
                    datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
                    + f"-{audit.head_hash[:12]}-{uuid.uuid4().hex[:8]}"
                )
                payload = {
                    "schema_version": 1,
                    "backup_id": backup_id,
                    "created_at": created_at,
                    "context": context,
                    "ledger": {
                        "filename": "receipts.jsonl",
                        "sha256": hashlib.sha256(ledger_bytes).hexdigest(),
                        "records": audit.records,
                        "head_hash": audit.head_hash,
                    },
                    "signing_state": {
                        "filename": self.signing_state_path.name,
                        "sha256": hashlib.sha256(signing_state_bytes).hexdigest(),
                    },
                }
                manifest = {
                    "payload": payload,
                    "signature_algorithm": SIGNATURE_ALGORITHM,
                    "key_id": self._key_id(),
                    "signature": self._sign(payload),
                }
                backup_dir.mkdir(parents=True, exist_ok=True)
                temporary = backup_dir / f".{backup_id}.tmp"
                final = backup_dir / backup_id
                temporary.mkdir()
                try:
                    self._write_durable(temporary / "receipts.jsonl", ledger_bytes)
                    self._write_durable(
                        temporary / self.signing_state_path.name,
                        signing_state_bytes,
                    )
                    self._write_durable(
                        temporary / "manifest.json",
                        canonical_json(manifest) + b"\n",
                    )
                    temporary.replace(final)
                except Exception:
                    for child in temporary.glob("*"):
                        child.unlink(missing_ok=True)
                    temporary.rmdir()
                    raise
                return {**manifest, "path": str(final)}

    def verify_and_restore_backup(self, backup_path: Path, restore_path: Path) -> dict[str, Any]:
        manifest_path = backup_path / "manifest.json"
        ledger_path = backup_path / "receipts.jsonl"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload = manifest["payload"]
        if manifest.get("signature_algorithm") != SIGNATURE_ALGORITHM:
            raise ValueError("unsupported backup signature algorithm")
        if manifest.get("key_id") != self._key_id():
            raise ValueError("backup key ID does not match the trusted signing root")
        self._verify_signature(payload, str(manifest.get("signature", "")))
        ledger_bytes = ledger_path.read_bytes()
        expected_digest = str(payload["ledger"]["sha256"])
        if hashlib.sha256(ledger_bytes).hexdigest() != expected_digest:
            raise ValueError("backup ledger digest mismatch")
        state_metadata = payload.get("signing_state", {})
        state_filename = str(state_metadata.get("filename", ""))
        if not state_filename or Path(state_filename).name != state_filename:
            raise ValueError("backup signing-state filename is invalid")
        signing_state_path = backup_path / state_filename
        signing_state_bytes = signing_state_path.read_bytes()
        expected_state_digest = str(state_metadata.get("sha256", ""))
        if hashlib.sha256(signing_state_bytes).hexdigest() != expected_state_digest:
            raise ValueError("backup signing-state digest mismatch")
        self._write_durable(restore_path, ledger_bytes)
        restore_state_path = restore_path.with_name(state_filename)
        self._write_durable(restore_state_path, signing_state_bytes)
        restored = ReceiptLedger(
            restore_path,
            signing_key_path=self.signing_key_path,
            signing_state_path=restore_state_path,
            signing_required=True,
        )
        audit = restored.audit()
        if not audit.valid:
            raise ValueError("restored receipt ledger failed verification")
        if audit.records != int(payload["ledger"]["records"]):
            raise ValueError("restored receipt count does not match the signed manifest")
        if audit.head_hash != payload["ledger"]["head_hash"]:
            raise ValueError("restored receipt head does not match the signed manifest")
        with restored._lock:
            with restored._process_lock():
                restored_state = restored._read_signing_state()
                if restored_state is None:
                    raise ValueError("restored receipt signing continuity state is missing")
                restored._validate_signing_state(restored_state)
        return {
            "status": "VERIFIED",
            "backup_id": payload["backup_id"],
            "backup_sha256": expected_digest,
            "records": audit.records,
            "head_hash": audit.head_hash,
            "signing_key_id": audit.signing_key_id,
        }

    def _signing_state_for_boundary(
        self,
        *,
        legacy_records: int,
        legacy_head_hash: str | None,
        activation_receipt_hash: str,
    ) -> dict[str, Any]:
        payload = {
            "schema_version": 1,
            "legacy_records": legacy_records,
            "legacy_head_hash": legacy_head_hash,
            "activation_receipt_hash": activation_receipt_hash,
            "key_id": self._key_id(),
        }
        return {
            "payload": payload,
            "signature_algorithm": SIGNATURE_ALGORITHM,
            "key_id": self._key_id(),
            "signature": self._sign(payload),
        }

    def _derive_signing_state(self) -> dict[str, Any]:
        records = self._read_records()
        for index, record in enumerate(records):
            if record.get("signature"):
                if record.get("body", {}).get("event") != "runtime_activation":
                    raise RuntimeError(
                        "the first signed receipt is not a runtime-activation boundary"
                    )
                return self._signing_state_for_boundary(
                    legacy_records=index,
                    legacy_head_hash=record.get("previous_hash"),
                    activation_receipt_hash=str(record["receipt_hash"]),
                )
        raise RuntimeError("the signed receipt ledger has no signed transition boundary")

    def _read_signing_state(self) -> dict[str, Any] | None:
        if not self.signing_state_path.exists():
            return None
        try:
            return dict(json.loads(self.signing_state_path.read_text(encoding="utf-8")))
        except (
            json.JSONDecodeError,
            UnicodeDecodeError,
            OSError,
            TypeError,
            ValueError,
        ) as error:
            raise RuntimeError("receipt signing continuity state is unreadable or invalid") from error

    def _write_signing_state(self, signing_state: dict[str, Any]) -> None:
        if self.signing_state_path.exists():
            raise RuntimeError("refusing to overwrite receipt signing continuity state")
        self._write_durable(
            self.signing_state_path,
            canonical_json(signing_state) + b"\n",
        )

    def _validate_signing_state(
        self,
        signing_state: dict[str, Any],
        *,
        records: list[dict[str, Any]] | None = None,
    ) -> None:
        try:
            payload = dict(signing_state["payload"])
            if int(payload.get("schema_version", 0)) != 1:
                raise ValueError("unsupported signing continuity-state schema")
            if signing_state.get("signature_algorithm") != SIGNATURE_ALGORITHM:
                raise ValueError("unsupported signing continuity-state algorithm")
            if signing_state.get("key_id") != self._key_id():
                raise ValueError("signing continuity state uses an untrusted key")
            if payload.get("key_id") != self._key_id():
                raise ValueError("signing continuity-state payload uses an untrusted key")
            self._verify_signature(payload, str(signing_state.get("signature", "")))
            legacy_records = int(payload["legacy_records"])
            if legacy_records < 0:
                raise ValueError("signing continuity state has a negative boundary")
            ledger_records = records if records is not None else self._read_records()
            if len(ledger_records) <= legacy_records:
                raise ValueError("receipt ledger no longer contains its signed transition")
            activation = ledger_records[legacy_records]
            if activation.get("body", {}).get("event") != "runtime_activation":
                raise ValueError("recorded signing transition is not a runtime activation")
            if activation.get("previous_hash") != payload.get("legacy_head_hash"):
                raise ValueError("receipt ledger legacy boundary changed")
            if activation.get("receipt_hash") != payload.get("activation_receipt_hash"):
                raise ValueError("receipt ledger signed transition changed")
            if activation.get("key_id") != payload.get("key_id") or not activation.get(
                "signature"
            ):
                raise ValueError("receipt ledger signed transition is not trusted")
        except (InvalidSignature, KeyError, TypeError, ValueError) as error:
            raise RuntimeError("receipt signing continuity state verification failed") from error

    def _read_records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [
            dict(json.loads(raw_line))
            for raw_line in self.path.read_text(encoding="utf-8").splitlines()
        ]

    def _append_locked(self, body: dict[str, Any]) -> str:
        previous_hash = self._last_hash()
        if self.signing_key_path is None:
            if self.signing_required:
                raise RuntimeError("receipt signing is required but no key file is configured")
            envelope = {"previous_hash": previous_hash, "body": body}
            receipt_hash = hashlib.sha256(canonical_json(envelope)).hexdigest()
            record = {**envelope, "receipt_hash": receipt_hash}
        else:
            payload = {
                "schema_version": 2,
                "previous_hash": previous_hash,
                "body": body,
            }
            receipt_hash = hashlib.sha256(canonical_json(payload)).hexdigest()
            record = {
                **payload,
                "receipt_hash": receipt_hash,
                "signature_algorithm": SIGNATURE_ALGORITHM,
                "key_id": self._key_id(),
                "signature": self._sign(payload),
            }
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(canonical_json(record).decode("utf-8") + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return receipt_hash

    def _audit_locked(self, *, allow_unsigned_head: bool = False) -> LedgerAudit:
        try:
            records = self._read_records()
        except (
            json.JSONDecodeError,
            TypeError,
            UnicodeDecodeError,
            ValueError,
        ):
            return self._invalid_audit(0, None, 0, 0)
        return self._audit_records_locked(
            records,
            allow_unsigned_head=allow_unsigned_head,
        )

    def _audit_records_locked(
        self,
        records: list[dict[str, Any]],
        *,
        allow_unsigned_head: bool = False,
    ) -> LedgerAudit:
        previous_hash: str | None = None
        count = 0
        signed_records = 0
        legacy_unsigned_records = 0
        seen_signed = False
        if not records:
            valid = not self.signing_required or allow_unsigned_head
            return LedgerAudit(
                valid=valid,
                records=0,
                head_hash=None,
                signed_records=0,
                legacy_unsigned_records=0,
                signature_status="UNSIGNED" if valid else "INVALID",
                signature_algorithm=None,
                signing_key_id=self._key_id() if self.signing_key_path else None,
            )
        for record in records:
            if record.get("previous_hash") != previous_hash:
                return self._invalid_audit(count, previous_hash, signed_records, legacy_unsigned_records)
            version = int(record.get("schema_version", 1))
            if version == 1:
                payload = {"previous_hash": previous_hash, "body": record.get("body")}
            elif version == 2:
                payload = {
                    "schema_version": 2,
                    "previous_hash": previous_hash,
                    "body": record.get("body"),
                }
            else:
                return self._invalid_audit(count, previous_hash, signed_records, legacy_unsigned_records)
            expected = hashlib.sha256(canonical_json(payload)).hexdigest()
            if record.get("receipt_hash") != expected:
                return self._invalid_audit(count, previous_hash, signed_records, legacy_unsigned_records)
            signature = record.get("signature")
            if signature:
                if (
                    version != 2
                    or record.get("signature_algorithm") != SIGNATURE_ALGORITHM
                    or self.signing_key_path is None
                    or record.get("key_id") != self._key_id()
                ):
                    return self._invalid_audit(count, previous_hash, signed_records, legacy_unsigned_records)
                try:
                    self._verify_signature(payload, str(signature))
                except (InvalidSignature, ValueError):
                    return self._invalid_audit(count, previous_hash, signed_records, legacy_unsigned_records)
                signed_records += 1
                seen_signed = True
            else:
                if seen_signed:
                    return self._invalid_audit(count, previous_hash, signed_records, legacy_unsigned_records)
                legacy_unsigned_records += 1
            previous_hash = expected
            count += 1
        signature_status = "SIGNED" if signed_records else "UNSIGNED"
        valid = not self.signing_required or (
            signature_status == "SIGNED" and bool(records[-1].get("signature"))
        )
        if allow_unsigned_head and signature_status == "UNSIGNED":
            valid = True
        return LedgerAudit(
            valid=valid,
            records=count,
            head_hash=previous_hash,
            signed_records=signed_records,
            legacy_unsigned_records=legacy_unsigned_records,
            signature_status=signature_status if valid else "INVALID",
            signature_algorithm=SIGNATURE_ALGORITHM if signed_records else None,
            signing_key_id=self._key_id() if self.signing_key_path else None,
        )

    def _audit_with_signing_state_locked(self) -> LedgerAudit:
        try:
            records = self._read_records()
        except (
            json.JSONDecodeError,
            TypeError,
            UnicodeDecodeError,
            ValueError,
        ):
            return self._invalid_audit(0, None, 0, 0)
        return self._audit_signing_state_locked(
            self._audit_records_locked(records),
            records=records,
        )

    def _audit_signing_state_locked(
        self,
        audit: LedgerAudit,
        *,
        records: list[dict[str, Any]] | None = None,
    ) -> LedgerAudit:
        if not audit.valid:
            return audit
        try:
            signing_state = self._read_signing_state()
            if signing_state is None:
                if audit.signed_records:
                    raise RuntimeError("receipt signing continuity state is missing")
                return audit
            self._validate_signing_state(signing_state, records=records)
        except RuntimeError:
            return self._invalid_audit(
                audit.records,
                audit.head_hash,
                audit.signed_records,
                audit.legacy_unsigned_records,
            )
        return audit

    def _invalid_audit(
        self,
        count: int,
        previous_hash: str | None,
        signed_records: int,
        legacy_unsigned_records: int,
    ) -> LedgerAudit:
        return LedgerAudit(
            valid=False,
            records=count,
            head_hash=previous_hash,
            signed_records=signed_records,
            legacy_unsigned_records=legacy_unsigned_records,
            signature_status="INVALID",
            signature_algorithm=SIGNATURE_ALGORITHM if signed_records else None,
            signing_key_id=self._key_id() if self.signing_key_path else None,
        )

    def _last_hash(self) -> str | None:
        record = self._last_record()
        return str(record["receipt_hash"]) if record is not None else None

    def _last_record(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        lines = self.path.read_text(encoding="utf-8").splitlines()
        if not lines:
            return None
        return dict(json.loads(lines[-1]))

    def _load_private_key(self) -> ec.EllipticCurvePrivateKey:
        if self._private_key is not None:
            return self._private_key
        if self.signing_key_path is None:
            raise RuntimeError("no receipt signing key is configured")
        try:
            loaded = serialization.load_pem_private_key(
                self.signing_key_path.read_bytes(),
                password=None,
            )
        except (OSError, ValueError, TypeError) as error:
            raise RuntimeError("the receipt signing key file is unreadable or invalid") from error
        if not isinstance(loaded, ec.EllipticCurvePrivateKey) or not isinstance(
            loaded.curve, ec.SECP256R1
        ):
            raise RuntimeError("the receipt signing key must be an ECDSA P-256 private key")
        self._private_key = loaded
        return loaded

    def _key_id(self) -> str:
        public_der = self._load_private_key().public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        return f"sha256:{hashlib.sha256(public_der).hexdigest()}"

    def _public_key_pem(self) -> str:
        return self._load_private_key().public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("ascii")

    def _sign(self, payload: dict[str, Any]) -> str:
        signature = self._load_private_key().sign(
            canonical_json(payload),
            ec.ECDSA(hashes.SHA256()),
        )
        return base64.b64encode(signature).decode("ascii")

    def _verify_signature(self, payload: dict[str, Any], signature: str) -> None:
        try:
            decoded = base64.b64decode(signature, validate=True)
        except ValueError as error:
            raise InvalidSignature from error
        self._load_private_key().public_key().verify(
            decoded,
            canonical_json(payload),
            ec.ECDSA(hashes.SHA256()),
        )

    @staticmethod
    def _write_durable(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        with temporary.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)

    def _append_unlocked(self, body: dict[str, Any]) -> str:
        previous_hash = self._last_hash()
        envelope = {"previous_hash": previous_hash, "body": body}
        receipt_hash = hashlib.sha256(canonical_json(envelope)).hexdigest()
        record = {**envelope, "receipt_hash": receipt_hash}
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(canonical_json(record).decode("utf-8") + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return receipt_hash

    @contextmanager
    def _process_lock(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        with lock_path.open("a+b", buffering=0) as handle:
            if os.name == "nt":
                import msvcrt

                self._acquire_windows_lock(handle, msvcrt)
                try:
                    yield
                finally:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _acquire_windows_lock(handle: BinaryIO, msvcrt_module: Any) -> None:
        deadline = time.monotonic() + WINDOWS_LOCK_TIMEOUT_SECONDS
        last_error: OSError | None = None

        while os.fstat(handle.fileno()).st_size < 1:
            try:
                handle.seek(0)
                handle.write(b"\0")
            except OSError as error:
                last_error = error
                if time.monotonic() >= deadline:
                    raise TimeoutError("timed out initializing the receipt ledger lock file") from last_error
                time.sleep(WINDOWS_LOCK_RETRY_SECONDS)

        while True:
            try:
                handle.seek(0)
                msvcrt_module.locking(handle.fileno(), msvcrt_module.LK_NBLCK, 1)
                return
            except OSError as error:
                last_error = error
                if time.monotonic() >= deadline:
                    raise TimeoutError("timed out acquiring the receipt ledger lock") from last_error
                time.sleep(WINDOWS_LOCK_RETRY_SECONDS)
