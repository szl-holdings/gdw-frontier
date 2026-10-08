from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .diagnostics import DiagnosticSnapshot

Decision = Literal["allow", "review", "withhold"]
SchedulerMode = Literal["interactive", "balanced", "throughput"]
ReceiptStatus = Literal["valid", "missing", "invalid"]
ActionType = Literal[
    "adjust_threshold",
    "adjust_worker_count",
    "block_weights",
    "increase_scrape_interval",
    "quarantine_route",
    "raise_alert",
    "record_exemplar",
]


class StepRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    session_id: str = Field(min_length=1, max_length=128)
    proposal_id: str = Field(min_length=1, max_length=128)
    objective: str = Field(min_length=1, max_length=2_000)
    confidence: float = Field(ge=0.0, le=1.0)
    receipt_present: bool = True
    scheduler_mode: SchedulerMode = "balanced"


class StepResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Decision
    scheduler_mode: SchedulerMode
    receipt_status: ReceiptStatus
    receipt_hash: str
    request_id: str
    trace_id: str | None
    session_ref: str
    status: Literal["MODELED"] = "MODELED"


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    service: Literal["gdw"]
    model_status: Literal["MODELED"] = "MODELED"


class BuildInfoResponse(BaseModel):
    service: Literal["gdw"] = "gdw"
    version: str
    source_sha: str
    image_digest: str
    deployment_mode: str
    receipt_signing: str
    receipt_signing_key_id: str | None
    model_status: Literal["MODELED"] = "MODELED"


class LedgerStatusResponse(BaseModel):
    valid: bool
    records: int
    head_hash: str | None
    signed_records: int
    legacy_unsigned_records: int
    signature_status: Literal["UNSIGNED", "SIGNED", "INVALID"]
    signature_algorithm: str | None
    signing_key_id: str | None
    model_status: Literal["MODELED"] = "MODELED"


class SignedReceiptProofResponse(BaseModel):
    valid: bool
    record: dict[str, Any]
    public_key_pem: str
    audit: dict[str, Any]
    model_status: Literal["MODELED"] = "MODELED"


class BackupResponse(BaseModel):
    status: Literal["VERIFIED"] = "VERIFIED"
    backup_id: str
    created_at: str
    ledger_sha256: str
    records: int
    head_hash: str
    signing_key_id: str
    model_status: Literal["MODELED"] = "MODELED"


class ProposedAction(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    type: ActionType
    target: str = Field(min_length=1, max_length=256)
    value: str | int | float | None = None
    rationale: str = Field(min_length=1, max_length=1_000)
    human_approval_required: bool = False


class CalibrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot: DiagnosticSnapshot
    k: int = Field(default=3, ge=1, le=20)


class CalibrationResponse(BaseModel):
    release_score: float = Field(ge=0.0, le=100.0)
    score_breakdown: dict
    similar_incidents: list[dict]
    actions: list[ProposedAction]
    status: Literal["MODELED"] = "MODELED"


class DecodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot: DiagnosticSnapshot


class DecodeResponse(BaseModel):
    incident_type: str
    severity: Literal["low", "medium", "high", "critical"]
    reversibility: Literal["none", "reversible", "partial", "blocking"]
    human_approval_required: bool
    status: Literal["MODELED"] = "MODELED"


class SimulationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot: DiagnosticSnapshot
    action: ProposedAction


class SimulationResponse(BaseModel):
    projected_snapshot: DiagnosticSnapshot
    projection_interval: dict[str, DiagnosticSnapshot]
    applied: bool
    assumptions: list[str]
    effect_model: dict[str, str | bool]
    projected_release_score: float = Field(ge=0.0, le=100.0)
    status: Literal["MODELED"] = "MODELED"


class PolicyCheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: ProposedAction
    receipt_present: bool = True
    approval_granted: bool = False


class PolicyCheckResponse(BaseModel):
    decision: Decision
    reasons: list[str]
    receipt_hash: str
    policy_revision: str
    policy_digest: str
    risk_class: str
    execution_mode: str
    status: Literal["MODELED"] = "MODELED"


class QuantCalibrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_threshold: float = Field(ge=0.0, le=1.0)
    predicted_confidence: float = Field(ge=0.0, le=1.0)
    observed_success: float = Field(ge=0.0, le=1.0)
    learning_rate: float = Field(default=0.1, gt=0.0, le=1.0)
    minimum: float = Field(default=0.0, ge=0.0, le=1.0)
    maximum: float = Field(default=1.0, ge=0.0, le=1.0)
    receipt_present: bool = True
    approval_granted: bool = False

    @model_validator(mode="after")
    def validate_bounds(self) -> "QuantCalibrationRequest":
        if self.minimum > self.maximum:
            raise ValueError("minimum threshold cannot exceed maximum threshold")
        return self


class QuantCalibrationResponse(BaseModel):
    current_threshold: float
    calibration_error: float
    proposed_threshold: float
    proposed_delta: float
    policy_decision: Decision
    reasons: list[str]
    receipt_hash: str
    policy_revision: str
    policy_digest: str
    status: Literal["MODELED"] = "MODELED"


class ExemplarRecordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot: DiagnosticSnapshot
    intervention: ProposedAction | None = None
    outcome: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    trust_score: float = Field(default=1.0, ge=0.0, le=1.0)
    risk_score: float = Field(default=0.0, ge=0.0, le=1.0)
    parent_record_hash: str | None = Field(default=None, min_length=64, max_length=64)
    tags: list[str] = Field(default_factory=list, max_length=20)
    receipt_present: bool = True


class ExemplarRecordResponse(BaseModel):
    decision: Decision
    reasons: list[str]
    record_hash: str | None
    created: bool
    receipt_hash: str
    policy_revision: str
    policy_digest: str
    status: Literal["MODELED"] = "MODELED"


class RiskControlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    nonconformity_scores: list[Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False)]] = Field(
        min_length=1, max_length=100_000
    )
    alpha: float = Field(default=0.1, gt=0.0, lt=1.0)
    minimum_samples: int = Field(default=20, ge=1, le=100_000)


class RiskControlResponse(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    threshold: float | None
    sample_count: int
    minimum_samples: int
    target_coverage: float
    evidence_status: Literal["MEASURED", "UNAVAILABLE"]
    assumptions: list[str]
    status: Literal["MODELED"] = "MODELED"
