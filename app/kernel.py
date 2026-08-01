from __future__ import annotations

from dataclasses import dataclass

from .models import Decision, ReceiptStatus
from .policy_bundle import PolicyBundle, load_policy_bundle


@dataclass(frozen=True)
class PolicyResult:
    decision: Decision
    reasons: tuple[str, ...]
    policy_revision: str
    policy_digest: str
    risk_class: str
    execution_mode: str


def decide(*, confidence: float, receipt_status: ReceiptStatus) -> Decision:
    """Apply a small deterministic MODELED policy; this is not a production trust claim."""
    if receipt_status != "valid":
        return "withhold"
    if confidence >= 0.85:
        return "allow"
    if confidence >= 0.60:
        return "review"
    return "withhold"


def check_policy(
    *,
    action_type: str,
    receipt_status: ReceiptStatus,
    human_approval_required: bool,
    approval_granted: bool,
    policy_bundle: PolicyBundle | None = None,
) -> PolicyResult:
    """Evaluate a proposal without executing it."""
    bundle = policy_bundle or load_policy_bundle()
    reasons: list[str] = []
    action_policy = bundle.actions.get(action_type)
    if action_policy is None:
        reasons.append("action type is not in the modeled allowlist")
    if receipt_status != "valid":
        reasons.append("input receipt is not valid")
    configured_approval = bool(action_policy and action_policy["approval_required"])
    if (configured_approval or human_approval_required) and not approval_granted:
        reasons.append("human approval is required")
    risk_class = str(action_policy["risk_class"]) if action_policy else "critical"
    execution_mode = str(action_policy["execution_mode"]) if action_policy else "prohibited"
    if any(reason != "human approval is required" for reason in reasons):
        decision: Decision = "withhold"
    elif reasons:
        decision = "review"
    else:
        decision = "allow"
        reasons.append("modeled policy checks passed")
    return PolicyResult(
        decision=decision,
        reasons=tuple(reasons),
        policy_revision=bundle.revision,
        policy_digest=bundle.digest,
        risk_class=risk_class,
        execution_mode=execution_mode,
    )
