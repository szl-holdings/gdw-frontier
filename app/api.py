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

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import CollectorRegistry, REGISTRY, multiprocess
from prometheus_client.openmetrics.exposition import CONTENT_TYPE_LATEST, generate_latest

from . import __version__
from .hybrid_scheduler import validate_mode
from .kernel import decide
from .logging_setup import configure_logging, log_event
from .models import (
    BackupResponse,
    BuildInfoResponse,
    LedgerStatusResponse,
    ReadinessResponse,
    SignedReceiptProofResponse,
    StepRequest,
    StepResponse,
)
from .persistence import ReceiptLedger
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

REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
AUTH_EXEMPT_PATHS = frozenset({"/build-info", "/healthz", "/metrics", "/readyz"})
TRUE_VALUES = frozenset({"1", "true", "yes"})
logger = configure_logging()
configure_tracing()
ledger = ReceiptLedger()
workspace = WorkspaceState()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    _validate_runtime_configuration()
    ledger.ensure_signed_head(_runtime_identity())
    audit = ledger.audit()
    log_event(
        logger,
        logging.INFO,
        "service_started",
        event="service_started",
        ledger_valid=audit.valid,
        ledger_records=audit.records,
        receipt_signature_status=audit.signature_status,
        receipt_signing_key_id=audit.signing_key_id,
    )
    try:
        yield
    finally:
        if os.getenv("PROMETHEUS_MULTIPROC_DIR"):
            multiprocess.mark_process_dead(os.getpid())
        log_event(logger, logging.INFO, "service_stopped", event="service_stopped")


app = FastAPI(
    title="GDW Self-Defending Observability Core",
    version="0.1.0",
    description="MODELED Governed Delta Workspace operational layer.",
    lifespan=lifespan,
)


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
    status = "ready" if ledger.audit().valid else "not_ready"
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

    receipt_hash = None
    if receipt_status == "valid":
        receipt_hash = ledger.append(
            {
                "model_status": "MODELED",
                "proposal_ref": hashlib.sha256(payload.proposal_id.encode("utf-8")).hexdigest(),
                "session_ref": request.state.session_ref,
                "decision": decision,
                "scheduler_mode": scheduler_mode,
                "request_id": request.state.request_id,
                "trace_id": trace_fields.get("trace_id"),
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
