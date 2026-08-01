from __future__ import annotations

from pydantic import ConfigDict

from gdw_x.domain.master_suite import (
    DeploymentMode,
    OrderIntent,
    RiskGateDecision,
    StrictModel,
)


class RiskControls(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    control_owner: str = "risk-office"
    revision: str = "MODELED-15C3-5-2026-07-29"
    max_order_size: float = 100.0
    max_notional: float = 100_000.0
    max_price_deviation: float = 0.05
    restricted_instruments: frozenset[str] = frozenset()
    authorized_actors: frozenset[str] = frozenset({"paper-runtime"})
    kill_switch_engaged: bool = False


class RiskGate15c35:
    """Independent, fail-closed pre-execution gate.

    The name describes an architectural analogy. It does not assert regulatory
    compliance, broker-dealer control, or market-access authorization.
    """

    def __init__(self, controls: RiskControls) -> None:
        self._controls = controls

    @property
    def controls(self) -> RiskControls:
        return self._controls

    def evaluate(
        self,
        intent: OrderIntent,
        *,
        manifest_valid: bool,
        manifest_expired: bool,
        parent_hash_matches: bool,
        approved_mode: DeploymentMode | None,
    ) -> RiskGateDecision:
        reasons: list[str] = []
        controls = self._controls

        if controls.control_owner != "risk-office":
            reasons.append("risk_control_owner_invalid")
        if controls.kill_switch_engaged:
            reasons.append("kill_switch_engaged")
        if intent.actor_identity not in controls.authorized_actors:
            reasons.append("actor_not_authorized")
        if not manifest_valid:
            reasons.append("invalid_manifest_signature")
        if manifest_expired:
            reasons.append("manifest_expired")
        if not parent_hash_matches:
            reasons.append("parent_runtime_hash_mismatch")
        if approved_mode is None or intent.requested_mode != approved_mode:
            reasons.append("deployment_mode_mismatch")
        if intent.requested_mode in {
            DeploymentMode.CANARY,
            DeploymentMode.LIVE,
        }:
            reasons.append("live_execution_authority_unavailable")
        if intent.requested_mode not in {
            DeploymentMode.PAPER,
            DeploymentMode.SHADOW,
        }:
            reasons.append("mode_not_simulation_eligible")
        if intent.size <= 0.0:
            reasons.append("non_positive_order_size")
        if intent.size > controls.max_order_size:
            reasons.append("max_order_size_breach")
        if abs(intent.size * intent.price) > controls.max_notional:
            reasons.append("max_notional_breach")
        if intent.symbol in controls.restricted_instruments:
            reasons.append("restricted_instrument")
        if intent.price_deviation > controls.max_price_deviation:
            reasons.append("erroneous_order_price_deviation")
        if intent.duplicate:
            reasons.append("duplicative_order")

        allowed = not reasons
        return RiskGateDecision(
            allow=allowed,
            reason_codes=reasons or ["modeled_pre_execution_controls_passed"],
            control_revision=controls.revision,
            requested_mode=intent.requested_mode,
            execution_authority=(
                "SIMULATION_ONLY" if allowed else "UNAVAILABLE"
            ),
        )
