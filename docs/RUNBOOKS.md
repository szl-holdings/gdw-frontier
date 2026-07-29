# GDW alert runbooks

These runbooks apply to the modeled local stack. Production remediation must use the target environment’s ownership and change controls.

## GDWHighP95Latency

1. Confirm the alert expression and affected route in Prometheus.
2. Open `GDW Live Operations`, then inspect p95/p99, in-flight requests, scheduler split, and error ratio.
3. Follow a metric exemplar into Tempo. Use its trace ID to open correlated Loki logs.
4. Check worker saturation with measured CPU/memory before changing worker count.
5. Record the measured cause. Do not raise the threshold to hide an unexplained regression.

## GDWHighErrorRate

1. Confirm whether failures are 4xx or 5xx and identify the templated route.
2. Inspect the error-only log stream and correlated traces.
3. Check receipt and readiness state.
4. Reproduce the failing request without copying secrets or raw session identifiers into tickets.
5. Roll back or fix the responsible release under normal change controls.

## GDWMissingReceipts

1. Treat the affected modeled decision as `withhold`.
2. Query `gdw_receipt_total{status="missing"}` and the decision log by request ID.
3. Verify the local ledger and `/readyz`.
4. Determine whether the caller explicitly omitted the receipt or the persistence path failed.
5. Preserve the evidence; do not synthesize a replacement receipt.

## GDWTelemetryAbsent

1. Check the Prometheus target page and the last successful scrape.
2. Query `/readyz` directly from the same network as Prometheus.
3. Inspect application and Prometheus logs.
4. Check scrape sample/label-limit failures before increasing a limit.
5. Mark observability `UNAVAILABLE` until a fresh scrape is personally observed.

