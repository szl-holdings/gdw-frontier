from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import Field

from gdw_x.control_plane.master_suite_service import (
    MasterSuiteError,
    MasterSuiteService,
)
from gdw_x.domain.master_suite import (
    FormulaObservation,
    OrderIntent,
    OutcomeObservation,
    StrategyMutation,
    StrictModel,
)

try:
    from opentelemetry import trace
except ImportError:
    trace = None


router = APIRouter(tags=["master-suite"])
service = MasterSuiteService()
PROJECT_ROOT = Path(__file__).resolve().parents[3]


class MutationEnvelope(StrictModel):
    mutation: StrategyMutation
    observation: FormulaObservation
    reference_pool: list[FormulaObservation] = Field(
        default_factory=list,
        max_length=10_000,
    )


class ManifestRequest(StrictModel):
    proposal_hash: str
    expires_in_seconds: int = Field(default=900, ge=60, le=3600)


class RuntimeAckRequest(StrictModel):
    manifest_hash: str
    runtime_instance: str = Field(min_length=1, max_length=128)


class RiskEvaluationRequest(StrictModel):
    manifest_hash: str
    order_intent: OrderIntent


def _correlation_id(request: Request) -> str:
    return request.headers.get("x-correlation-id") or str(uuid4())


@contextmanager
def _span(name: str, attributes: dict[str, Any]) -> Iterator[None]:
    if trace is None:
        yield
        return
    tracer = trace.get_tracer("gdw_x.master_suite", "2026-07-29")
    with tracer.start_as_current_span(name) as span:
        for key, value in attributes.items():
            if value is not None:
                span.set_attribute(key, value)
        yield


def _raise_http(error: MasterSuiteError) -> None:
    statuses = {
        "proposal_not_found": 404,
        "manifest_not_found": 404,
        "journal_integrity_failed": 503,
        "signing_key_unavailable": 503,
    }
    raise HTTPException(
        status_code=statuses.get(error.code, 409),
        detail={"code": error.code, "message": str(error)},
    )


@router.get("/master-suite", include_in_schema=False)
def master_suite_console() -> FileResponse:
    return FileResponse(PROJECT_ROOT / "app" / "static" / "master-suite.html")


@router.get("/v1/master-suite/status")
def master_suite_status() -> dict[str, Any]:
    return service.dashboard_state()


@router.post("/v1/strategy/mutations", status_code=201)
@router.post("/v1/calibration/proposals", status_code=201)
def submit_mutation(
    envelope: MutationEnvelope,
    request: Request,
) -> dict[str, Any]:
    correlation_id = _correlation_id(request)
    with _span(
        "master_suite.submit_mutation",
        {
            "service_role": "calibration_plane",
            "formula_id": envelope.mutation.formula_id,
            "deployment_mode": envelope.mutation.requested_mode.value,
            "decision_state": "proposed",
        },
    ):
        try:
            result = service.submit_mutation(
                envelope.mutation,
                envelope.observation,
                envelope.reference_pool,
                correlation_id=correlation_id,
            )
        except MasterSuiteError as error:
            _raise_http(error)
    return {**result, "correlation_id": correlation_id}


@router.post("/v1/calibration/simulate/{proposal_hash}")
def simulate(proposal_hash: str, request: Request) -> dict[str, Any]:
    correlation_id = _correlation_id(request)
    with _span(
        "master_suite.simulate",
        {
            "service_role": "calibration_plane",
            "decision_state": "simulation",
        },
    ):
        try:
            result = service.simulate(
                proposal_hash,
                correlation_id=correlation_id,
            )
        except MasterSuiteError as error:
            _raise_http(error)
    return {**result, "correlation_id": correlation_id}


@router.post("/v1/governance/decisions/{proposal_hash}")
def decide(proposal_hash: str, request: Request) -> dict[str, Any]:
    correlation_id = _correlation_id(request)
    with _span(
        "master_suite.governance_decision",
        {
            "service_role": "governance_plane",
            "decision_state": "evaluating",
        },
    ):
        try:
            result = service.decide(
                proposal_hash,
                correlation_id=correlation_id,
            )
        except MasterSuiteError as error:
            _raise_http(error)
    return {
        "decision": result.model_dump(mode="json"),
        "correlation_id": correlation_id,
    }


@router.post("/v1/runtime/manifests", status_code=201)
def issue_manifest(
    payload: ManifestRequest,
    request: Request,
) -> dict[str, Any]:
    correlation_id = _correlation_id(request)
    with _span(
        "master_suite.issue_manifest",
        {
            "service_role": "governance_plane",
            "decision_state": "manifest_request",
        },
    ):
        try:
            result = service.issue_manifest(
                payload.proposal_hash,
                expires_in_seconds=payload.expires_in_seconds,
                correlation_id=correlation_id,
            )
        except MasterSuiteError as error:
            _raise_http(error)
    return {
        "manifest": result.model_dump(mode="json"),
        "correlation_id": correlation_id,
    }


@router.post("/v1/runtime/acks")
def acknowledge(
    payload: RuntimeAckRequest,
    request: Request,
) -> dict[str, Any]:
    correlation_id = _correlation_id(request)
    with _span(
        "master_suite.runtime_ack",
        {
            "service_role": "runtime_plane",
            "decision_state": "apply_validation",
        },
    ):
        try:
            result = service.acknowledge(
                payload.manifest_hash,
                payload.runtime_instance,
                correlation_id=correlation_id,
            )
        except MasterSuiteError as error:
            _raise_http(error)
    return {
        "ack": result.model_dump(mode="json"),
        "correlation_id": correlation_id,
    }


@router.post("/v1/risk-gate/evaluate")
def evaluate_risk(
    payload: RiskEvaluationRequest,
    request: Request,
) -> dict[str, Any]:
    correlation_id = _correlation_id(request)
    with _span(
        "master_suite.risk_gate",
        {
            "service_role": "risk_gate_plane",
            "deployment_mode": payload.order_intent.requested_mode.value,
            "decision_state": "pre_execution",
        },
    ):
        try:
            result = service.evaluate_risk(
                payload.order_intent,
                payload.manifest_hash,
                correlation_id=correlation_id,
            )
        except MasterSuiteError as error:
            _raise_http(error)
    return {
        "risk_gate": result.model_dump(mode="json"),
        "correlation_id": correlation_id,
    }


@router.post("/v1/outcomes", status_code=201)
def record_outcome(
    outcome: OutcomeObservation,
    request: Request,
) -> dict[str, Any]:
    correlation_id = _correlation_id(request)
    with _span(
        "master_suite.record_outcome",
        {
            "service_role": "lake_memory_plane",
            "decision_state": "outcome_observed",
        },
    ):
        try:
            result = service.record_outcome(
                outcome,
                correlation_id=correlation_id,
            )
        except MasterSuiteError as error:
            _raise_http(error)
    return {**result, "correlation_id": correlation_id}


@router.get("/v1/dashboard/state")
def dashboard_state() -> dict[str, Any]:
    with _span(
        "master_suite.dashboard_state",
        {
            "service_role": "observability_plane",
            "decision_state": "read_only",
        },
    ):
        return service.dashboard_state()


@router.get("/v1/dashboard/alerts")
def dashboard_alerts() -> dict[str, Any]:
    with _span(
        "master_suite.dashboard_alerts",
        {
            "service_role": "observability_plane",
            "decision_state": "read_only",
        },
    ):
        return {
            "truth_status": "MODELED",
            "alerts": service.alerts(),
        }


@router.get("/v1/lake/exemplars")
def lake_exemplars() -> dict[str, Any]:
    with _span(
        "master_suite.lake_exemplars",
        {
            "service_role": "lake_memory_plane",
            "decision_state": "read_only",
        },
    ):
        return {
            "truth_status": "MODELED",
            "exemplars": service.exemplars(),
        }
