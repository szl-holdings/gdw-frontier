from __future__ import annotations

from typing import Any


def instrument_fastapi(app: Any) -> dict[str, str]:
    """Instrument FastAPI when the OTel integration is installed.

    Missing instrumentation is exposed as UNAVAILABLE rather than silently
    represented as active telemetry.
    """

    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    except ImportError:
        status = {
            "status": "UNAVAILABLE",
            "reason": "opentelemetry-instrumentation-fastapi_not_installed",
        }
        app.state.gdwx_otel = status
        return status

    if getattr(app.state, "gdwx_otel_instrumented", False):
        return {"status": "VERIFIED", "reason": "already_instrumented"}

    FastAPIInstrumentor.instrument_app(
        app,
        excluded_urls="healthz,readyz,metrics",
    )
    app.state.gdwx_otel_instrumented = True
    status = {
        "status": "VERIFIED",
        "reason": "fastapi_instrumentor_registered",
    }
    app.state.gdwx_otel = status
    return status
