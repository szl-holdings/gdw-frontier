from __future__ import annotations

from datetime import datetime

from gdw_x.domain.contracts import ApprovalManifest, DeploymentMode
from gdw_x.governance.receipts import ManifestAuthenticator


class RiskGateway:
    """Authorize configuration activation only; this class cannot submit orders."""

    def __init__(self, authenticator: ManifestAuthenticator) -> None:
        self.authenticator = authenticator

    def authorize_activation(
        self,
        manifest: ApprovalManifest,
        *,
        now: datetime,
    ) -> tuple[bool, str]:
        if not self.authenticator.verify_manifest(manifest):
            return False, "invalid_manifest_signature"
        if manifest.expires_at <= now:
            return False, "approval_expired"
        if manifest.approved_mode not in {DeploymentMode.PAPER, DeploymentMode.SHADOW}:
            return False, "market_authority_unavailable"
        return True, "paper_shadow_activation_authorized"
