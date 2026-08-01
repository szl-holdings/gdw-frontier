from __future__ import annotations

import hashlib
import logging
import os
import re
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Callable

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import CollectorRegistry, REGISTRY, multiprocess
from prometheus_client.openmetrics.exposition import CONTENT_TYPE_LATEST, generate_latest

from . import __version__
from .anomaly_decoder import decode_incident
from .benchmarks import benchmark_decoder, op_cal_eval
from .calibration_plane import calibrate as run_calibration
from .diagnostics import DiagnosticSnapshot
from .exemplar_store import ExemplarStore
from .hybrid_scheduler import validate_mode
from .incident_memory import find_similar
from .kernel import check_policy, decide
from .logging_setup import configure_logging, log_event
from .models import (
    BackupResponse,
    BuildInfoResponse,
    CalibrationRequest,
    CalibrationResponse,
    DecodeRequest,
    DecodeResponse,
    ExemplarRecordRequest,
    ExemplarRecordResponse,
    LedgerStatusResponse,
    PolicyCheckRequest,
    PolicyCheckResponse,
    QuantCalibrationRequest,
    QuantCalibrationResponse,
    ReadinessResponse,
    RiskControlRequest,
    RiskControlResponse,
    SimulationRequest,
    SimulationResponse,
    SignedReceiptProofResponse,
    StepRequest,
    StepResponse,
)
from .model_calibration_loop import propose_threshold_update
from .policy_bundle import load_policy_bundle
from .persistence import ReceiptLedger
from .provenance import governance_envelope, subject_digest
from .quant_core import release_score_breakdown
from .risk_control import conformal_risk_threshold
from .runtime import MasterSuiteProcessLease, governed_worker_count
from .simulator import simulate_intervention
from .telemetry import (
    DECISION_TOTAL,
    EXEMPLAR_LABEL,
    INFLIGHT_REQUESTS,
    RECEIPT_TOTAL,
    REQUEST_LATENCY,
    REQUEST_TOTAL,
    SCHEDULER_MODE_TOTAL,
)
from .tracing import configure_tracing, current_trace_fields, traced_span
from .workspace import WorkspaceState
from gdw_x.apps.calibration_api.router import router as calibration_router
from gdw_x.apps.master_suite_api.router import router as master_suite_router
from gdw_x.telemetry.otel import instrument_fastapi

REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
AUTH_EXEMPT_PATHS = frozenset(
    {
        "/build-info",
        "/calibration",
        "/healthz",
        "/master-suite",
        "/metrics",
        "/readyz",
    }
)
TRUE_VALUES = frozenset({"1", "true", "yes"})
logger = configure_logging()
configure_tracing()
ledger = ReceiptLedger()
exemplar_store = ExemplarStore()
policy_bundle = load_policy_bundle()
workspace = WorkspaceState()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    lease = MasterSuiteProcessLease()
    lease.acquire()
    try:
        _validate_runtime_configuration()
        ledger.ensure_signed_head(_runtime_identity())
        exemplar_store.ensure_signed_head(_runtime_identity())
        audit = ledger.audit()
        memory_valid, memory_records = exemplar_store.verify()
        log_event(
            logger,
            logging.INFO,
            "service_started",
            event="service_started",
            ledger_valid=audit.valid,
            ledger_records=audit.records,
            receipt_signature_status=audit.signature_status,
            receipt_signing_key_id=audit.signing_key_id,
            memory_valid=memory_valid,
            memory_records=memory_records,
            policy_revision=policy_bundle.revision,
            policy_digest=policy_bundle.digest,
        )
        try:
            yield
        finally:
            if os.getenv("PROMETHEUS_MULTIPROC_DIR"):
                multiprocess.mark_process_dead(os.getpid())
            log_event(logger, logging.INFO, "service_stopped", event="service_stopped")
    finally:
        lease.release()


app = FastAPI(
    title="GDW-X Governed Decision Workspace",
    version=__version__,
    description="MODELED modular control plane for governed operational intelligence.",
    lifespan=lifespan,
)
app.include_router(calibration_router)
app.include_router(master_suite_router)
instrument_fastapi(app)


def _request_id(request: Request) -> str:
    supplied = request.headers.get("x-request-id", "")
    return supplied if REQUEST_ID_PATTERN.fullmatch(supplied) else uuid.uuid4().hex


def _route_template(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return str(path) if path else "unmatched"


def _session_ref(session_id: str) -> str:
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:16]


def _auth_required() -> bool:
    return os.getenv("GDW_REQUIRE_AUTH", "").strip().lower() in TRUE_VALUES


def _configured_api_token() -> str:
    token_file = os.getenv("GDW_API_TOKEN_FILE", "").strip()
    if token_file:
        return Path(token_file).read_text(encoding="utf-8").strip()
    return os.getenv("GDW_API_TOKEN", "")


def _validate_runtime_configuration() -> None:
    governed_worker_count()
    ledger.validate_configuration()
    if not _auth_required():
        return
    token = _configured_api_token()
    if len(token) < 32:
        raise RuntimeError("GDW_API_TOKEN must contain at least 32 characters when GDW_REQUIRE_AUTH is enabled")


def _runtime_identity() -> dict[str, str]:
    return {
        "service": "gdw",
        "version": __version__,
        "source_sha": os.getenv("GDW_SOURCE_SHA", "UNAVAILABLE"),
        "image_digest": os.getenv("GDW_IMAGE_DIGEST", "UNAVAILABLE"),
        "deployment_mode": os.getenv("GDW_DEPLOYMENT_MODE", "MODELED_LOCAL"),
    }


def _has_valid_bearer_token(request: Request) -> bool:
    if not _auth_required() or request.url.path in AUTH_EXEMPT_PATHS:
        return True
    configured = _configured_api_token()
    scheme, separator, supplied = request.headers.get("authorization", "").partition(" ")
    return (
        separator == " "
        and scheme.lower() == "bearer"
        and bool(configured)
        and secrets.compare_digest(supplied, configured)
    )


@app.middleware("http")
async def enforce_api_auth(request: Request, call_next: Callable) -> Response:
    if _has_valid_bearer_token(request):
        return await call_next(request)
    return JSONResponse(
        status_code=401,
        content={"detail": "authentication required"},
        headers={"WWW-Authenticate": "Bearer"},
    )


@app.middleware("http")
async def observe_request(request: Request, call_next: Callable) -> Response:
    if request.url.path == "/metrics":
        return await call_next(request)

    request.state.request_id = _request_id(request)
    started = time.perf_counter()
    INFLIGHT_REQUESTS.inc()
    status_code = 500
    span_attributes = {
        "http.request.method": request.method,
        "url.path": request.url.path,
        "gdw.request_id": request.state.request_id,
    }
    span_fields: dict[str, str] = {}
    try:
        with traced_span("http.server", **span_attributes) as active_span_fields:
            span_fields = active_span_fields
            try:
                response = await call_next(request)
                status_code = response.status_code
            except Exception:
                log_event(
                    logger,
                    logging.ERROR,
                    "request_failed",
                    event="request_failed",
                    request_id=request.state.request_id,
                    endpoint=request.url.path,
                )
                response = JSONResponse(
                    status_code=500,
                    content={"detail": "internal server error", "request_id": request.state.request_id},
                )
            response.headers["x-request-id"] = request.state.request_id
            return response
    finally:
        elapsed = time.perf_counter() - started
        route = _route_template(request)
        status_class = f"{status_code // 100}xx"
        REQUEST_TOTAL.labels(method=request.method, route=route, status_class=status_class).inc()
        latency = REQUEST_LATENCY.labels(method=request.method, route=route)
        exemplar = {EXEMPLAR_LABEL: span_fields["trace_id"]} if span_fields else None
        latency.observe(elapsed, exemplar=exemplar)
        INFLIGHT_REQUESTS.dec()
        log_event(
            logger,
            logging.INFO,
            "request_completed",
            event="request_completed",
            request_id=request.state.request_id,
            endpoint=route,
            method=request.method,
            status=status_code,
            duration_seconds=round(elapsed, 6),
            session_ref=getattr(request.state, "session_ref", None),
            **span_fields,
        )


@app.get("/healthz", response_model=ReadinessResponse)
async def healthz() -> ReadinessResponse:
    return ReadinessResponse(status="ready", service="gdw")


@app.get("/readyz", response_model=ReadinessResponse)
async def readyz() -> ReadinessResponse:
    valid = ledger.audit().valid
    memory_valid, _ = exemplar_store.verify()
    status = "ready" if valid and memory_valid else "not_ready"
    return ReadinessResponse(status=status, service="gdw")


@app.get("/build-info", response_model=BuildInfoResponse)
async def build_info() -> BuildInfoResponse:
    signing_algorithm, signing_key_id = ledger.signing_identity()
    return BuildInfoResponse(
        version=__version__,
        source_sha=os.getenv("GDW_SOURCE_SHA", "UNAVAILABLE"),
        image_digest=os.getenv("GDW_IMAGE_DIGEST", "UNAVAILABLE"),
        deployment_mode=os.getenv("GDW_DEPLOYMENT_MODE", "MODELED_LOCAL"),
        receipt_signing=signing_algorithm or "UNAVAILABLE",
        receipt_signing_key_id=signing_key_id,
    )


@app.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    registry = REGISTRY
    if os.getenv("PROMETHEUS_MULTIPROC_DIR"):
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
    return Response(content=generate_latest(registry), media_type=CONTENT_TYPE_LATEST)


@app.get("/v1/gdw/state")
async def state() -> dict:
    return workspace.summary()


@app.get("/v1/gdw/ledger", response_model=LedgerStatusResponse)
async def ledger_status() -> LedgerStatusResponse:
    return LedgerStatusResponse(**ledger.audit().to_dict())


@app.get("/v1/gdw/receipt", response_model=SignedReceiptProofResponse)
async def signed_receipt_proof() -> SignedReceiptProofResponse:
    try:
        return SignedReceiptProofResponse(**ledger.head_proof())
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@app.post("/v1/gdw/backup", response_model=BackupResponse)
async def create_backup() -> BackupResponse:
    configured = os.getenv("GDW_BACKUP_DIR", "").strip()
    if not configured:
        raise HTTPException(status_code=503, detail="GDW_BACKUP_DIR is not configured")
    try:
        manifest = ledger.create_backup(Path(configured), _runtime_identity())
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    payload = manifest["payload"]
    return BackupResponse(
        backup_id=payload["backup_id"],
        created_at=payload["created_at"],
        ledger_sha256=payload["ledger"]["sha256"],
        records=payload["ledger"]["records"],
        head_hash=payload["ledger"]["head_hash"],
        signing_key_id=manifest["key_id"],
    )


@app.post("/v1/gdw/step", response_model=StepResponse)
async def step(payload: StepRequest, request: Request) -> StepResponse:
    request.state.session_ref = _session_ref(payload.session_id)
    scheduler_mode = validate_mode(payload.scheduler_mode)
    receipt_status = "valid" if payload.receipt_present else "missing"
    decision = decide(confidence=payload.confidence, receipt_status=receipt_status)
    trace_fields = current_trace_fields()

    receipt_hash = ledger.append(
        {
            **governance_envelope(
                event="step_decision",
                subject=payload.model_dump(mode="json"),
                policy=policy_bundle,
                decision=decision,
                reasons=(
                    ["input receipt is not valid"]
                    if receipt_status != "valid"
                    else ["modeled confidence policy evaluated"]
                ),
                details={
                    "proposal_ref": hashlib.sha256(payload.proposal_id.encode("utf-8")).hexdigest(),
                    "session_ref": request.state.session_ref,
                    "input_receipt_status": receipt_status,
                    "scheduler_mode": scheduler_mode,
                    "request_id": request.state.request_id,
                    "trace_id": trace_fields.get("trace_id"),
                },
            ),
            "runtime_identity": _runtime_identity(),
        }
    )

    DECISION_TOTAL.labels(decision=decision).inc()
    RECEIPT_TOTAL.labels(status=receipt_status).inc()
    SCHEDULER_MODE_TOTAL.labels(mode=scheduler_mode).inc()
    workspace.record({"decision": decision, "scheduler_mode": scheduler_mode, "receipt_status": receipt_status})
    log_event(
        logger,
        logging.INFO,
        "decision_recorded",
        event="decision_recorded",
        request_id=request.state.request_id,
        session_ref=request.state.session_ref,
        endpoint="/v1/gdw/step",
        decision=decision,
        scheduler_mode=scheduler_mode,
        receipt_status=receipt_status,
        receipt_hash=receipt_hash,
        **trace_fields,
    )
    return StepResponse(
        decision=decision,
        scheduler_mode=scheduler_mode,
        receipt_status=receipt_status,
        receipt_hash=receipt_hash,
        request_id=request.state.request_id,
        trace_id=trace_fields.get("trace_id"),
        session_ref=request.state.session_ref,
    )


@app.post("/v1/gdw/calibrate", response_model=CalibrationResponse)
async def calibrate(payload: CalibrationRequest) -> CalibrationResponse:
    result = run_calibration(payload.snapshot, exemplar_store.load(), k=payload.k)
    return CalibrationResponse.model_validate(result)


@app.post("/v1/gdw/decode", response_model=DecodeResponse)
async def decode(payload: DecodeRequest) -> DecodeResponse:
    return DecodeResponse.model_validate(decode_incident(payload.snapshot))


@app.post("/v1/gdw/simulate", response_model=SimulationResponse)
async def simulate(payload: SimulationRequest) -> SimulationResponse:
    simulation = simulate_intervention(payload.snapshot, payload.action.model_dump())
    projected = simulation["projected_snapshot"]
    score = release_score_breakdown(
        p95_latency_ms=projected.p95_latency_ms,
        error_rate=projected.error_rate,
        missing_receipt_rate=projected.missing_receipt_rate,
        weight_validation_passed=projected.weight_validation_passed,
        cardinality_risk=min(1.0, float(projected.prometheus_series_count or 0) / 500_000.0),
    )
    return SimulationResponse(
        projected_snapshot=projected,
        projection_interval=simulation["projection_interval"],
        applied=simulation["applied"],
        assumptions=simulation["assumptions"],
        effect_model=simulation["effect_model"],
        projected_release_score=score["score"],
    )


@app.get("/v1/gdw/score/release")
async def score_release(
    p95_latency_ms: float = Query(ge=0.0),
    error_rate: float = Query(ge=0.0, le=1.0),
    missing_receipt_rate: float = Query(ge=0.0, le=1.0),
    weight_validation_passed: bool = True,
    cardinality_risk: float = Query(default=0.0, ge=0.0, le=1.0),
) -> dict:
    return {
        **release_score_breakdown(
            p95_latency_ms=p95_latency_ms,
            error_rate=error_rate,
            missing_receipt_rate=missing_receipt_rate,
            weight_validation_passed=weight_validation_passed,
            cardinality_risk=cardinality_risk,
        ),
        "status": "MODELED",
    }


@app.get("/v1/gdw/exemplars/search")
async def search_exemplars(
    snapshot_id: str = Query(min_length=1, max_length=128),
    p50_latency_ms: float = Query(ge=0.0),
    p95_latency_ms: float = Query(ge=0.0),
    p99_latency_ms: float = Query(ge=0.0),
    error_rate: float = Query(ge=0.0, le=1.0),
    inflight_requests: int = Query(ge=0),
    missing_receipt_rate: float = Query(ge=0.0, le=1.0),
    prometheus_series_count: int = Query(default=0, ge=0),
    log_volume_per_min: int = Query(default=0, ge=0),
    weight_validation_passed: bool = True,
    k: int = Query(default=5, ge=1, le=20),
) -> dict:
    snapshot = DiagnosticSnapshot(
        snapshot_id=snapshot_id,
        p50_latency_ms=p50_latency_ms,
        p95_latency_ms=p95_latency_ms,
        p99_latency_ms=p99_latency_ms,
        error_rate=error_rate,
        inflight_requests=inflight_requests,
        missing_receipt_rate=missing_receipt_rate,
        prometheus_series_count=prometheus_series_count,
        log_volume_per_min=log_volume_per_min,
        weight_validation_passed=weight_validation_passed,
    )
    matches = find_similar(snapshot, exemplar_store.load(), k=k)
    return {
        "matches": [
            {
                "similarity": round(similarity, 6),
                "snapshot_id": row["snapshot"]["snapshot_id"],
                "record_hash": row.get("record_hash"),
                "intervention": row.get("intervention"),
                "outcome": row.get("outcome", {}),
                "parent_record_hash": row.get("parent_record_hash"),
                "tags": row.get("tags", []),
                "retrieval": row.get("retrieval", {}),
            }
            for similarity, row in matches
        ],
        "status": "MODELED",
    }


@app.post("/v1/gdw/exemplars", response_model=ExemplarRecordResponse)
async def record_exemplar(payload: ExemplarRecordRequest) -> ExemplarRecordResponse:
    receipt_status = "valid" if payload.receipt_present else "missing"
    policy = check_policy(
        action_type="record_exemplar",
        receipt_status=receipt_status,
        human_approval_required=False,
        approval_granted=False,
        policy_bundle=policy_bundle,
    )
    receipt_hash = ledger.append(
        governance_envelope(
            event="exemplar_record_decision",
            subject=payload.model_dump(mode="json"),
            policy=policy_bundle,
            decision=policy.decision,
            reasons=list(policy.reasons),
            details={
                "snapshot_id": payload.snapshot.snapshot_id,
                "input_receipt_status": receipt_status,
            },
        )
    )
    record_hash = None
    created = False
    if policy.decision == "allow":
        record_hash, created = exemplar_store.append_once(
            {
                "snapshot": payload.snapshot.model_dump(mode="json"),
                "intervention": (
                    payload.intervention.model_dump(mode="json") if payload.intervention else None
                ),
                "outcome": payload.outcome,
                "trust_score": payload.trust_score,
                "risk_score": payload.risk_score,
                "parent_record_hash": payload.parent_record_hash,
                "tags": sorted(set(payload.tags)),
                "governance_receipt_hash": receipt_hash,
            },
            idempotency_key=subject_digest(payload.model_dump(mode="json")),
        )
    return ExemplarRecordResponse(
        decision=policy.decision,
        reasons=list(policy.reasons),
        record_hash=record_hash,
        created=created,
        receipt_hash=receipt_hash,
        policy_revision=policy.policy_revision,
        policy_digest=policy.policy_digest,
    )


@app.post("/v1/gdw/policy/check", response_model=PolicyCheckResponse)
async def policy_check(payload: PolicyCheckRequest) -> PolicyCheckResponse:
    receipt_status = "valid" if payload.receipt_present else "missing"
    policy = check_policy(
        action_type=payload.action.type,
        receipt_status=receipt_status,
        human_approval_required=payload.action.human_approval_required,
        approval_granted=payload.approval_granted,
        policy_bundle=policy_bundle,
    )
    receipt_hash = ledger.append(
        governance_envelope(
            event="policy_check",
            subject=payload.model_dump(mode="json"),
            policy=policy_bundle,
            decision=policy.decision,
            reasons=list(policy.reasons),
            details={"input_receipt_status": receipt_status},
        )
    )
    return PolicyCheckResponse(
        decision=policy.decision,
        reasons=list(policy.reasons),
        receipt_hash=receipt_hash,
        policy_revision=policy.policy_revision,
        policy_digest=policy.policy_digest,
        risk_class=policy.risk_class,
        execution_mode=policy.execution_mode,
    )


@app.post("/v1/gdw/quant/calibrate", response_model=QuantCalibrationResponse)
async def quant_calibrate(payload: QuantCalibrationRequest) -> QuantCalibrationResponse:
    proposal = propose_threshold_update(
        current=payload.current_threshold,
        predicted_confidence=payload.predicted_confidence,
        observed_success=payload.observed_success,
        learning_rate=payload.learning_rate,
        minimum=payload.minimum,
        maximum=payload.maximum,
    )
    receipt_status = "valid" if payload.receipt_present else "missing"
    policy = check_policy(
        action_type="adjust_threshold",
        receipt_status=receipt_status,
        human_approval_required=True,
        approval_granted=payload.approval_granted,
        policy_bundle=policy_bundle,
    )
    receipt_hash = ledger.append(
        governance_envelope(
            event="quant_calibration_proposal",
            subject=proposal,
            policy=policy_bundle,
            decision=policy.decision,
            reasons=list(policy.reasons),
            details={"input_receipt_status": receipt_status},
        )
    )
    return QuantCalibrationResponse(
        current_threshold=proposal["current_threshold"],
        calibration_error=proposal["calibration_error"],
        proposed_threshold=proposal["proposed_threshold"],
        proposed_delta=proposal["proposed_delta"],
        policy_decision=policy.decision,
        reasons=list(policy.reasons),
        receipt_hash=receipt_hash,
        policy_revision=policy.policy_revision,
        policy_digest=policy.policy_digest,
    )


@app.post("/v1/gdw/quant/risk-control", response_model=RiskControlResponse)
async def quant_risk_control(payload: RiskControlRequest) -> RiskControlResponse:
    result = conformal_risk_threshold(
        payload.nonconformity_scores,
        alpha=payload.alpha,
        minimum_samples=payload.minimum_samples,
    )
    return RiskControlResponse.model_validate(result)


@app.get("/v1/gdw/benchmarks/decoder")
async def decoder_benchmark() -> dict:
    return benchmark_decoder()


@app.post("/v1/gdw/benchmarks/opcal")
async def opcal_benchmark(payload: CalibrationRequest) -> dict:
    calibration = run_calibration(payload.snapshot, exemplar_store.load(), k=payload.k)
    return op_cal_eval(payload.snapshot, calibration["actions"])
