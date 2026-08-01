from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class DeploymentMode(str, Enum):
    OFFLINE = "offline"
    PAPER = "paper"
    SHADOW = "shadow"
    CANARY = "canary"
    LIVE = "live"
    QUARANTINED = "quarantined"


class CalibrationState(str, Enum):
    HEALTHY = "healthy"
    WATCH = "watch"
    DEGRADED = "degraded"
    UNKNOWN = "unknown"
    UNSAFE = "unsafe"


class Decision(str, Enum):
    ACCEPT = "accept"
    REJECT = "reject"
    REVIEW = "review"
    QUARANTINE = "quarantine"


class ProposalState(str, Enum):
    DRAFT = "draft"
    SIMULATED = "simulated"
    REJECTED = "rejected"
    APPROVED_FOR_PAPER = "approved_for_paper"
    PAPER_ACTIVE = "paper_active"
    APPROVED_FOR_SHADOW = "approved_for_shadow"
    SHADOW_ACTIVE = "shadow_active"
    QUARANTINED = "quarantined"
    ROLLED_BACK = "rolled_back"
    RETIRED = "retired"


def canonical_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def canonical_digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


class DriftVector(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_drift: float = Field(default=0.0, ge=0.0)
    parameter_drift: float = Field(default=0.0, ge=0.0)
    residual_drift: float = Field(default=0.0, ge=0.0)
    calibration_drift: float = Field(default=0.0, ge=0.0)
    regime_novelty: float = Field(default=0.0, ge=0.0)
    execution_drift: float = Field(default=0.0, ge=0.0)


class FormulaObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    formula_id: str = Field(min_length=1, max_length=128)
    formula_version: str = Field(min_length=1, max_length=64)
    parameter_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    timestamp: datetime
    predicted: float
    realized: float | None = None
    raw_confidence: float = Field(ge=0.0, le=1.0)
    calibrated_confidence: float = Field(ge=0.0, le=1.0)
    regime_id: str = Field(min_length=1, max_length=128)
    latency_ms: float = Field(ge=0.0)
    feature_schema_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    data_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    tags: dict[str, str] = Field(default_factory=dict, max_length=20)


class CalibrationProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    proposal_id: str = Field(min_length=1, max_length=128)
    created_at: datetime
    formula_id: str = Field(min_length=1, max_length=128)
    from_version: str = Field(min_length=1, max_length=64)
    candidate_version: str = Field(min_length=1, max_length=64)
    proposed_parameters: dict[str, float] = Field(default_factory=dict, max_length=64)
    proposed_confidence_transform: dict[str, Any] = Field(default_factory=dict)
    evidence_hashes: list[str] = Field(default_factory=list, max_length=128)
    drift: DriftVector
    expected_utility_delta: float
    uncertainty: float = Field(ge=0.0, le=1.0)
    reversible: bool = True
    requested_mode: DeploymentMode
    parent_runtime_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    proposer_identity: str = Field(min_length=1, max_length=256)

    def canonical_hash(self) -> str:
        return canonical_digest(self.model_dump(mode="json"))


class SimulationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    observations: int = Field(ge=0)
    directional_accuracy: float = Field(ge=0.0, le=1.0)
    calibration_error: float = Field(ge=0.0)
    abstention_rate: float = Field(ge=0.0, le=1.0)
    false_action_rate: float = Field(ge=0.0, le=1.0)
    utility: float
    maximum_drawdown: float = Field(ge=0.0)
    governance_blocks: int = Field(ge=0)
    fault_tags: list[str] = Field(default_factory=list)
    evidence_status: Literal["MODELED"] = "MODELED"


class GovernanceDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: Decision
    policy_version: str
    reason_codes: list[str]
    approved_mode: DeploymentMode | None = None
    signer_ref: str
    signature_algorithm: Literal["HMAC-SHA256"] = "HMAC-SHA256"
    signing_key_source: Literal["environment", "ephemeral_process"]
    signature: str
    status: Literal["MODELED"] = "MODELED"


class ApprovalManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["gdwx.approval-manifest.v1"] = "gdwx.approval-manifest.v1"
    proposal_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    parent_runtime_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_runtime_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    approved_mode: DeploymentMode
    policy_version: str
    issued_at: datetime
    expires_at: datetime
    signer_ref: str
    signature_algorithm: Literal["HMAC-SHA256"] = "HMAC-SHA256"
    signing_key_source: Literal["environment", "ephemeral_process"]
    signature: str

    def signed_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"signature"})

    def canonical_hash(self) -> str:
        return canonical_digest(self.model_dump(mode="json"))


class RuntimeStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runtime_instance: str
    active_runtime_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    active_mode: DeploymentMode
    active_proposal_hash: str | None = None
    applied_manifest_hash: str | None = None


class RuntimeAck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_hash: str
    runtime_instance: str
    previous_runtime_hash: str
    active_runtime_hash: str
    active_mode: DeploymentMode
    status: Literal["applied", "rejected", "rolled_back"]
    reason: str
    applied_at: datetime
    manifest_hash: str

    def canonical_hash(self) -> str:
        return canonical_digest(self.model_dump(mode="json"))


class CalibrationEpisode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    episode_id: str
    recorded_at: datetime
    formula_id: str
    formula_version: str
    regime_id: str
    parameter_hash: str
    drift: DriftVector
    proposal_hash: str
    manifest_hash: str
    runtime_instance: str
    observed_runtime_hash: str
    decision: Decision
    deployment_mode: DeploymentMode
    observed_utility: float
    predicted_utility: float
    rollback_status: Literal["not_required", "rolled_back", "pending"]
    evidence_hashes: list[str]
    uncertainty: float = Field(ge=0.0, le=1.0)
    parent_episode_hash: str | None = None

    def canonical_hash(self) -> str:
        return canonical_digest(self.model_dump(mode="json"))
