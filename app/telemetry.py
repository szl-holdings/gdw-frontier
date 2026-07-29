from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

REQUEST_TOTAL = Counter(
    "gdw_request_total",
    "Completed GDW HTTP requests.",
    ["method", "route", "status_class"],
)
REQUEST_LATENCY = Histogram(
    "gdw_request_latency_seconds",
    "GDW HTTP request duration.",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 1.5, 2.5, 5.0),
)
INFLIGHT_REQUESTS = Gauge(
    "gdw_inflight_requests",
    "GDW requests currently executing.",
    multiprocess_mode="livesum",
)
DECISION_TOTAL = Counter(
    "gdw_decision_total",
    "MODELED GDW decisions.",
    ["decision"],
)
RECEIPT_TOTAL = Counter(
    "gdw_receipt_total",
    "GDW local receipt outcomes.",
    ["status"],
)
SCHEDULER_MODE_TOTAL = Counter(
    "gdw_scheduler_mode_total",
    "GDW scheduler selections.",
    ["mode"],
)
TRACE_SPAN_LATENCY = Histogram(
    "gdw_trace_span_latency_seconds",
    "Latency of bounded GDW span categories.",
    ["span_name"],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5),
)
LOG_LINES_TOTAL = Counter(
    "gdw_log_lines_total",
    "Structured GDW log records emitted.",
    ["level"],
)
BUILD_INFO = Gauge(
    "gdw_build_info",
    "GDW modeled build information.",
    ["version", "model_status"],
    multiprocess_mode="livesum",
)
BUILD_INFO.labels(version="0.1.0", model_status="MODELED").set(1)

EXEMPLAR_LABEL = "trace_id"
