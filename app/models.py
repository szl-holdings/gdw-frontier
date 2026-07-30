from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Decision = Literal["allow", "review", "withhold"]
SchedulerMode = Literal["interactive", "balanced", "throughput"]
ReceiptStatus = Literal["valid", "missing", "invalid"]


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
    receipt_hash: str | None
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
