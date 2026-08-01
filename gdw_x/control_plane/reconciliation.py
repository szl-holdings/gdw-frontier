from __future__ import annotations

from datetime import datetime

from gdw_x.data_plane.risk_gateway import RiskGateway
from gdw_x.domain.contracts import ApprovalManifest, RuntimeStatus


def reconcile(
    manifest: ApprovalManifest,
    runtime: RuntimeStatus,
    *,
    gateway: RiskGateway,
    now: datetime,
) -> dict:
    authorized, reason = gateway.authorize_activation(manifest, now=now)
    if not authorized:
        return {"action": "reject", "reason": reason}
    if runtime.active_runtime_hash == manifest.target_runtime_hash:
        return {"action": "noop", "reason": "already_converged"}
    if runtime.active_runtime_hash != manifest.parent_runtime_hash:
        return {"action": "quarantine", "reason": "runtime_parent_hash_mismatch"}
    return {
        "action": "apply",
        "reason": "authenticated_paper_shadow_manifest",
        "target_hash": manifest.target_runtime_hash,
        "mode": manifest.approved_mode.value,
    }
