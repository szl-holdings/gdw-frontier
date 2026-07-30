from __future__ import annotations

import base64
import hashlib
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app.persistence import SIGNATURE_ALGORITHM, canonical_json


def verify_signed_receipt(proof: dict[str, Any]) -> dict[str, Any]:
    if proof.get("valid") is not True:
        raise ValueError("receipt proof is not marked valid")
    record = dict(proof["record"])
    if record.get("schema_version") != 2:
        raise ValueError("receipt proof does not use signed schema version 2")
    if record.get("signature_algorithm") != SIGNATURE_ALGORITHM:
        raise ValueError("receipt proof has an unsupported signature algorithm")
    payload = {
        "schema_version": 2,
        "previous_hash": record.get("previous_hash"),
        "body": record.get("body"),
    }
    receipt_hash = hashlib.sha256(canonical_json(payload)).hexdigest()
    if record.get("receipt_hash") != receipt_hash:
        raise ValueError("receipt proof hash does not match its canonical payload")
    public_pem = str(proof["public_key_pem"]).encode("ascii")
    public_key = serialization.load_pem_public_key(public_pem)
    if not isinstance(public_key, ec.EllipticCurvePublicKey) or not isinstance(
        public_key.curve, ec.SECP256R1
    ):
        raise ValueError("receipt proof does not contain a P-256 public key")
    public_der = public_key.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    key_id = f"sha256:{hashlib.sha256(public_der).hexdigest()}"
    if record.get("key_id") != key_id:
        raise ValueError("receipt proof key ID does not match its public key")
    signature = base64.b64decode(str(record["signature"]), validate=True)
    public_key.verify(signature, canonical_json(payload), ec.ECDSA(hashes.SHA256()))
    return {
        "valid": True,
        "receipt_hash": receipt_hash,
        "previous_hash": record.get("previous_hash"),
        "signature_algorithm": SIGNATURE_ALGORITHM,
        "signing_key_id": key_id,
        "body": record.get("body"),
    }
