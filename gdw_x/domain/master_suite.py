from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def canonical_json(value: Any) -> bytes:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def canonical_hash(value: Any) -> str:
    return sha256(canonical_json(value)).hexdigest()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DeploymentMode(str, Enum):
    OFFLINE = "offline"
    PAPER = "paper"
    SHADOW = "shadow"
    CANARY = "canary"
    LIVE = "live"
    QUARANTINED = "quarantined"


class GovernanceDisposition(str, Enum):
    ACCEPT = "accept"
    REJECT = "reject"
    REVIEW = "review"
    QUARANTINE = "quarantine"


class DriftVector(StrictModel):
    input_drift: float = Field(default=0.0, ge=0.0, le=1.0)
    parameter_drift: float = Field(default=0.0, ge=0.0, le=1.0)
    residual_drift: float = Field(default=0.0, ge=0.0, le=1.0)
    calibration_drift: float = Field(default=0.0, ge=0.0, le=1.0)
    regime_novelty: float = Field(default=0.0, ge=0.0, le=1.0)
    execution_drift: float = Field(default=0.0, ge=0.0, le=1.0)
    localization_payload: dict[str, Any] = Field(default_factory=dict)

    @property
    def maximum(self) -> float:
        return max(
            self.input_drift,
            self.parameter_drift,
            self.residual_drift,
            self.calibration_drift,
            self.regime_novelty,
            self.execution_drift,
        )


class FormulaObservation(StrictModel):
    observation_id: UUID = Field(default_factory=uuid4)
    formula_id: str = Field(min_length=1, max_length=128)
    formula_version: str = Field(min_length=1, max_length=64)
    timestamp: datetime = Field(default_factory=utc_now)
    predicted: float
    realized: float | None = None
    raw_confidence: float = Field(ge=0.0, le=1.0)
    regime_id: str = Field(default="unknown", min_length=1, max_length=128)
    features: dict[str, float] = Field(default_factory=dict)
    feature_schema_hash: str = Field(min_length=1, max_length=128)
    data_snapshot_hash: str = Field(min_length=1, max_length=128)
    latency_ms: float = Field(default=0.0, ge=0.0)


class DriftLocalization(StrictModel):
    method: Literal["dasc_style_empirical_conformal"] = (
        "dasc_style_empirical_conformal"
    )
    truth_status: Literal["MODELED"] = "MODELED"
    reference_window_hash: str
    reference_size: int = Field(ge=0)
    effective_sample_size: float = Field(ge=0.0)
    evidence_quality: float = Field(ge=0.0, le=1.0)
    dimensions: dict[str, dict[str, float | int | str]]
    drift: DriftVector
    interval_lower: float | None = None
    interval_upper: float | None = None
    interval_width: float | None = Field(default=None, ge=0.0)
    target_coverage: float = Field(ge=0.0, le=1.0)
    empirical_coverage: float | None = Field(default=None, ge=0.0, le=1.0)
    coverage_deviation: float | None = Field(default=None, ge=0.0, le=1.0)
    calibrated_confidence: float = Field(ge=0.0, le=1.0)
    abstain: bool
    alert_family: Literal[
        "conformal_watch",
        "conformal_degraded",
        "conformal_unsafe",
        "confidence_anomaly",
        "stale_calibration_pool",
        "none",
    ]
    recommended_next_step: Literal[
        "continue",
        "monitor",
        "simulate_and_review",
        "quarantine_or_rollback",
        "recalibrate",
    ]
    limitations: list[str] = Field(default_factory=list)


class StrategyMutation(StrictModel):
    mutation_id: UUID = Field(default_factory=uuid4)
    strategy_id: str = Field(min_length=1, max_length=128)
    formula_id: str = Field(min_length=1, max_length=128)
    from_version: str = Field(min_length=1, max_length=64)
    candidate_version: str = Field(min_length=1, max_length=64)
    parameter_diff: dict[str, float]
    evidence_hashes: list[str]
    drift_localization: DriftLocalization | None = None
    confidence_delta: float = 0.0
    simulation_summary: dict[str, Any] = Field(default_factory=dict)
    requested_mode: DeploymentMode
    parent_runtime_hash: str = Field(min_length=1, max_length=128)
    requestor_identity: str = Field(min_length=1, max_length=128)
    reversibility_plan: str | None = Field(default=None, max_length=2048)
    created_at: datetime = Field(default_factory=utc_now)

    def proposal_payload(self) -> dict[str, Any]:
        return self.model_dump(
            mode="json",
            exclude={"mutation_id", "created_at"},
        )

    def proposal_hash(self) -> str:
        return canonical_hash(self.proposal_payload())


class GovernanceDecision(StrictModel):
    decision_id: UUID = Field(default_factory=uuid4)
    proposal_hash: str
    decision: GovernanceDisposition
    policy_version: str
    reason_codes: list[str]
    signer: str
    signature: str
    approved_mode: DeploymentMode | None = None
    issued_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime

    def unsigned_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"signature"})


class RuntimeManifest(StrictModel):
    manifest_hash: str
    proposal_hash: str
    decision_id: UUID
    parent_runtime_hash: str
    target_runtime_hash: str
    approved_mode: DeploymentMode
    issued_at: datetime
    expires_at: datetime
    signer: str
    signature: str
    truth_status: Literal["MODELED"] = "MODELED"

    def unsigned_payload(self) -> dict[str, Any]:
        return self.model_dump(
            mode="json",
            exclude={"manifest_hash", "signature"},
        )


class RuntimeAck(StrictModel):
    ack_id: UUID = Field(default_factory=uuid4)
    manifest_hash: str
    runtime_instance: str = Field(min_length=1, max_length=128)
    previous_runtime_hash: str
    active_runtime_hash: str
    applied_at: datetime = Field(default_factory=utc_now)
    status: Literal["applied", "rejected", "rolled_back"]
    reason_codes: list[str] = Field(default_factory=list)
    deployment_mode: DeploymentMode
    broker_submission: Literal[False] = False


class OrderIntent(StrictModel):
    intent_id: UUID = Field(default_factory=uuid4)
    actor_identity: str = Field(min_length=1, max_length=128)
    symbol: str = Field(min_length=1, max_length=64)
    size: float
    price: float = Field(gt=0.0)
    price_deviation: float = Field(default=0.0, ge=0.0)
    requested_mode: DeploymentMode
    duplicate: bool = False


class RiskGateDecision(StrictModel):
    allow: bool
    reason_codes: list[str]
    control_revision: str
    evaluated_at: datetime = Field(default_factory=utc_now)
    requested_mode: DeploymentMode
    execution_authority: Literal["SIMULATION_ONLY", "UNAVAILABLE"]
    broker_submission: Literal[False] = False


class OutcomeObservation(StrictModel):
    outcome_id: UUID = Field(default_factory=uuid4)
    proposal_hash: str
    forecast_loss: float = Field(ge=0.0)
    calibration_error: float = Field(ge=0.0)
    latency_p95_ms: float = Field(ge=0.0)
    error_rate: float = Field(ge=0.0, le=1.0)
    risk_events: int = Field(ge=0)
    execution_slippage: float | None = None
    realized_utility: float | None = None
    observed_at: datetime = Field(default_factory=utc_now)
    truth_status: Literal["MEASURED", "MODELED"] = "MEASURED"
