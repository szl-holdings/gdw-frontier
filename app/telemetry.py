from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

from .version import VERSION
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
BUILD_INFO.labels(version=VERSION, model_status="MODELED").set(1)

CALIBRATION_DRIFT = Gauge(
    "gdw_calibration_drift",
    "MODELED calibration drift by bounded dimension and band.",
    ["dimension", "band"],
    multiprocess_mode="livemostrecent",
)
CALIBRATION_EVENTS = Counter(
    "gdw_calibration_events_total",
    "Calibration control-plane events.",
    ["event", "outcome", "mode"],
)
RUNTIME_CONVERGENCE = Histogram(
    "gdw_runtime_convergence_seconds",
    "Paper or shadow runtime convergence latency.",
    ["mode"],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5),
)
CALIBRATION_RECEIPT_FAILURES = Counter(
    "gdw_calibration_receipt_failures_total",
    "Calibration manifest or reconciliation failures.",
    ["reason"],
)

EXEMPLAR_LABEL = "trace_id"
