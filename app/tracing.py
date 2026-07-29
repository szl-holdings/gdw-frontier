from __future__ import annotations

import os
import time
from contextlib import contextmanager
from typing import Any, Iterator

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import SERVICE_NAME, SERVICE_VERSION, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from . import __version__
from .telemetry import EXEMPLAR_LABEL, TRACE_SPAN_LATENCY

_configured = False


def configure_tracing() -> None:
    global _configured
    if _configured:
        return

    provider = TracerProvider(
        resource=Resource.create(
            {
                SERVICE_NAME: os.getenv("OTEL_SERVICE_NAME", "gdw"),
                SERVICE_VERSION: __version__,
                "gdw.model_status": "MODELED",
            }
        )
    )
    if os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"):
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    _configured = True


def current_trace_fields() -> dict[str, str]:
    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return {}
    return {
        "trace_id": format(context.trace_id, "032x"),
        "span_id": format(context.span_id, "016x"),
    }


@contextmanager
def traced_span(span_name: str, **attributes: Any) -> Iterator[dict[str, str]]:
    tracer = trace.get_tracer("gdw")
    with tracer.start_as_current_span(span_name, attributes=attributes):
        fields = current_trace_fields()
        histogram = TRACE_SPAN_LATENCY.labels(span_name=span_name)
        started = time.perf_counter()
        try:
            yield fields
        finally:
            elapsed = time.perf_counter() - started
            exemplar = {EXEMPLAR_LABEL: fields["trace_id"]} if fields else None
            histogram.observe(elapsed, exemplar=exemplar)
