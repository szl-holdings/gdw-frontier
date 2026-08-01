from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime
from typing import Any, Literal

from gdw_x.domain.contracts import ApprovalManifest, DeploymentMode, canonical_bytes, canonical_digest


class ManifestAuthenticator:
    """Local HMAC authentication. This is not an independently verifiable signature."""

    def __init__(self, key: bytes | None = None, *, source: str | None = None) -> None:
        self.key = key or secrets.token_bytes(32)
        self.source: Literal["environment", "ephemeral_process"] = (
            "environment" if source == "environment" else "ephemeral_process"
        )
        self.signer_ref = hashlib.sha256(self.key).hexdigest()[:16]

    def sign(self, payload: dict[str, Any]) -> str:
        return hmac.new(self.key, canonical_bytes(payload), hashlib.sha256).hexdigest()

    def verify(self, payload: dict[str, Any], signature: str) -> bool:
        return hmac.compare_digest(self.sign(payload), signature)

    def issue_manifest(
        self,
        *,
        proposal_hash: str,
        parent_runtime_hash: str,
        target_runtime_hash: str,
        approved_mode: DeploymentMode,
        policy_version: str,
        issued_at: datetime,
        expires_at: datetime,
    ) -> ApprovalManifest:
        unsigned = {
            "schema_version": "gdwx.approval-manifest.v1",
            "proposal_hash": proposal_hash,
            "parent_runtime_hash": parent_runtime_hash,
            "target_runtime_hash": target_runtime_hash,
            "approved_mode": approved_mode.value,
            "policy_version": policy_version,
            "issued_at": issued_at.isoformat().replace("+00:00", "Z"),
            "expires_at": expires_at.isoformat().replace("+00:00", "Z"),
            "signer_ref": self.signer_ref,
            "signature_algorithm": "HMAC-SHA256",
            "signing_key_source": self.source,
        }
        return ApprovalManifest.model_validate({**unsigned, "signature": self.sign(unsigned)})

    def verify_manifest(self, manifest: ApprovalManifest) -> bool:
        return self.verify(manifest.signed_payload(), manifest.signature)


def deterministic_receipt_hash(
    *,
    event: str,
    subject_hash: str,
    state: str,
    policy_version: str,
) -> str:
    return canonical_digest(
        {
            "schema_version": "gdwx.content-receipt.v1",
            "event": event,
            "subject_hash": subject_hash,
            "state": state,
            "policy_version": policy_version,
        }
    )
