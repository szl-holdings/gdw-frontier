from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DiagnosticSnapshot(BaseModel):
    """Bounded operational inputs for MODELED GDW-X decisions."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)

    snapshot_id: str = Field(min_length=1, max_length=128)
    p50_latency_ms: float = Field(ge=0.0)
    p95_latency_ms: float = Field(ge=0.0)
    p99_latency_ms: float = Field(ge=0.0)
    error_rate: float = Field(ge=0.0, le=1.0)
    inflight_requests: int = Field(ge=0)
    missing_receipt_rate: float = Field(ge=0.0, le=1.0)
    prometheus_series_count: int | None = Field(default=None, ge=0)
    log_volume_per_min: int | None = Field(default=None, ge=0)
    weight_validation_passed: bool
    observed_at: datetime | None = None
    source_ref: str | None = Field(default=None, max_length=256)
