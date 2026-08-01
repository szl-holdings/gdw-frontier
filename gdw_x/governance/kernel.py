from __future__ import annotations

from dataclasses import dataclass

from gdw_x.domain.contracts import (
    CalibrationProposal,
    Decision,
    DeploymentMode,
    SimulationResult,
)


@dataclass(frozen=True)
class CalibrationPolicy:
    version: str
    allowed_auto_modes: frozenset[DeploymentMode]
    maximum_uncertainty: float
    maximum_false_action_rate: float
    require_reversibility: bool
    forbidden_parameter_names: frozenset[str]


def default_policy() -> CalibrationPolicy:
    return CalibrationPolicy(
        version="szl-calibration/2026-07-29",
        allowed_auto_modes=frozenset({DeploymentMode.PAPER, DeploymentMode.SHADOW}),
        maximum_uncertainty=0.30,
        maximum_false_action_rate=0.35,
        require_reversibility=True,
        forbidden_parameter_names=frozenset(
            {
                "credit_limit",
                "capital_limit",
                "kill_switch",
                "maximum_order_size",
                "maximum_position",
                "risk_limit",
            }
        ),
    )


class GovernanceKernel:
    def __init__(self, policy: CalibrationPolicy | None = None) -> None:
        self.policy = policy or default_policy()

    def evaluate(
        self,
        proposal: CalibrationProposal,
        simulations: list[SimulationResult],
    ) -> tuple[Decision, list[str], DeploymentMode | None]:
        reasons: list[str] = []
        protected = sorted(set(proposal.proposed_parameters) & self.policy.forbidden_parameter_names)
        if protected:
            reasons.append("attempt_to_modify_protected_risk_parameter")
            return Decision.QUARANTINE, reasons, None
        if proposal.requested_mode not in self.policy.allowed_auto_modes:
            reasons.append("mode_requires_independent_approval")
        if proposal.uncertainty > self.policy.maximum_uncertainty:
            reasons.append("uncertainty_above_policy")
        if self.policy.require_reversibility and not proposal.reversible:
            reasons.append("proposal_not_reversible")
        if not proposal.evidence_hashes:
            reasons.append("missing_evidence")
        if not simulations:
            reasons.append("missing_simulation")
        if any(result.false_action_rate > self.policy.maximum_false_action_rate for result in simulations):
            reasons.append("false_action_rate_above_policy")
        if reasons:
            return Decision.REVIEW, reasons, None
        return Decision.ACCEPT, ["policy_pass"], proposal.requested_mode
