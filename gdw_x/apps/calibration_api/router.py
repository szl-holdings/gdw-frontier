from __future__ import annotations

import os
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from app.telemetry import (
    CALIBRATION_DRIFT,
    CALIBRATION_EVENTS,
    CALIBRATION_RECEIPT_FAILURES,
    RUNTIME_CONVERGENCE,
)
from gdw_x.control_plane.calibration_service import CalibrationService
from gdw_x.domain.contracts import CalibrationProposal

router = APIRouter()
service = CalibrationService(Path(os.getenv("GDW_DATA_DIR", "data")) / "calibration-plane")
CONSOLE_PATH = Path(__file__).resolve().parents[3] / "app" / "static" / "calibration-console.html"


class SimulationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    scenario_ids: list[str] = Field(min_length=1, max_length=32)


class ProposalReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class ManifestRequest(ProposalReference):
    ttl_seconds: int = Field(default=900, ge=1, le=3_600)


class ApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class OutcomeRequest(ProposalReference):
    manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    runtime_instance: str = Field(min_length=1, max_length=128)
    observed_runtime_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    regime_id: str = Field(min_length=1, max_length=128)
    parameter_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_utility: float
    rollback_status: str = Field(pattern=r"^(not_required|rolled_back|pending)$")
    parent_episode_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


def _error(error: Exception) -> HTTPException:
    if isinstance(error, KeyError):
        return HTTPException(status_code=404, detail=str(error).strip("'"))
    if isinstance(error, PermissionError):
        return HTTPException(status_code=403, detail=str(error))
    return HTTPException(status_code=422, detail=str(error))


@router.get("/calibration", include_in_schema=False)
async def calibration_console() -> FileResponse:
    return FileResponse(CONSOLE_PATH)


@router.get("/v1/calibration/status")
async def calibration_status() -> dict:
    return service.status()


@router.post("/v1/calibration/proposals")
async def create_proposal(proposal: CalibrationProposal) -> dict:
    result = service.create_proposal(proposal)
    for dimension, value in proposal.drift.model_dump().items():
        band = "high" if value >= 0.6 else "watch" if value >= 0.3 else "low"
        CALIBRATION_DRIFT.labels(dimension=dimension, band=band).set(value)
    CALIBRATION_EVENTS.labels(
        event="proposal",
        outcome="accepted",
        mode=proposal.requested_mode.value,
    ).inc()
    return result


@router.post("/v1/calibration/simulate")
async def simulate_proposal(payload: SimulationRequest) -> dict:
    try:
        result = service.simulate(payload.proposal_hash, payload.scenario_ids)
    except (KeyError, ValueError) as error:
        CALIBRATION_EVENTS.labels(event="simulation", outcome="rejected", mode="offline").inc()
        raise _error(error) from error
    CALIBRATION_EVENTS.labels(event="simulation", outcome="accepted", mode="offline").inc()
    return result


@router.post("/v1/calibration/decisions")
async def decide_proposal(payload: ProposalReference) -> dict:
    try:
        decision = service.decide(payload.proposal_hash)
    except KeyError as error:
        raise _error(error) from error
    CALIBRATION_EVENTS.labels(
        event="decision",
        outcome=decision.decision.value,
        mode=decision.approved_mode.value if decision.approved_mode else "offline",
    ).inc()
    return decision.model_dump(mode="json")


@router.post("/v1/calibration/manifests")
async def issue_manifest(payload: ManifestRequest) -> dict:
    try:
        manifest = service.issue_manifest(payload.proposal_hash, payload.ttl_seconds)
    except (KeyError, PermissionError, ValueError) as error:
        raise _error(error) from error
    CALIBRATION_EVENTS.labels(
        event="manifest",
        outcome="accepted",
        mode=manifest.approved_mode.value,
    ).inc()
    return {
        "manifest_hash": manifest.canonical_hash(),
        "manifest": manifest.model_dump(mode="json"),
        "status": "MODELED",
    }


@router.post("/v1/calibration/apply")
async def apply_manifest(payload: ApplyRequest) -> dict:
    started = time.perf_counter()
    try:
        ack = service.apply_manifest(payload.manifest_hash)
    except KeyError as error:
        CALIBRATION_RECEIPT_FAILURES.labels(reason="unknown_manifest").inc()
        raise _error(error) from error
    if ack.active_mode.value in {"paper", "shadow"}:
        RUNTIME_CONVERGENCE.labels(mode=ack.active_mode.value).observe(time.perf_counter() - started)
    outcome = "accepted" if ack.status == "applied" else "rejected"
    CALIBRATION_EVENTS.labels(event="apply", outcome=outcome, mode=ack.active_mode.value).inc()
    if ack.status != "applied":
        CALIBRATION_RECEIPT_FAILURES.labels(reason=ack.reason).inc()
    return {**ack.model_dump(mode="json"), "ack_hash": ack.canonical_hash(), "model_status": "MODELED"}


@router.post("/v1/calibration/outcomes")
async def record_outcome(payload: OutcomeRequest) -> dict:
    try:
        return service.record_outcome(
            proposal_hash=payload.proposal_hash,
            manifest_hash=payload.manifest_hash,
            runtime_instance=payload.runtime_instance,
            observed_runtime_hash=payload.observed_runtime_hash,
            regime_id=payload.regime_id,
            parameter_hash=payload.parameter_hash,
            observed_utility=payload.observed_utility,
            rollback_status=payload.rollback_status,
            parent_episode_hash=payload.parent_episode_hash,
        )
    except (KeyError, PermissionError) as error:
        raise _error(error) from error


@router.get("/v1/calibration/exemplars/search")
async def search_exemplars(
    formula_id: str = Query(min_length=1, max_length=128),
    regime_id: str = Query(min_length=1, max_length=128),
    proposal_hash: str = Query(pattern=r"^[0-9a-f]{64}$"),
    k: int = Query(default=5, ge=1, le=20),
) -> dict:
    try:
        matches = service.search_exemplars(
            formula_id=formula_id,
            regime_id=regime_id,
            proposal_hash=proposal_hash,
            k=k,
        )
    except KeyError as error:
        raise _error(error) from error
    return {"matches": matches, "status": "MODELED"}
