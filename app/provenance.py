from __future__ import annotations

import hashlib
from typing import Any

from .persistence import canonical_json
from .policy_bundle import PolicyBundle


def subject_digest(subject: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(subject)).hexdigest()


def governance_envelope(
    *,
    event: str,
    subject: dict[str, Any],
    policy: PolicyBundle,
    decision: str,
    reasons: list[str],
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create an in-toto-inspired local envelope without claiming attestation compliance."""
    return {
        "schema_version": "gdw.governance-envelope.v1",
        "model_status": "MODELED",
        "event": event,
        "subject": {"sha256": subject_digest(subject)},
        "policy": {
            "schema_version": policy.schema_version,
            "revision": policy.revision,
            "sha256": policy.digest,
        },
        "decision": decision,
        "reasons": reasons,
        "details": details or {},
    }
