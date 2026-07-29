# High-cardinality and multi-tenant scaling posture

Status: **MODELED**

## Stage 1 — single-instance discipline

- Keep route labels templated and every other label enumerated.
- Preserve the CI AST audit and live series budgets.
- Keep short retention and WAL compression.
- Measure ingestion samples/second, active series, query latency, memory, disk growth, and recovery time.
- Reject a release if the scrape exceeds the configured sample or label limits.

Promotion evidence: sustained load test, restart/recovery test, query-latency report, disk-growth report, and a reviewed cardinality baseline.

## Stage 2 — workload segmentation

- Separate production, bench, and synthetic traffic into explicit scrape jobs.
- Assign per-job sample and label budgets.
- Use distinct service accounts and network policy.
- Do not put tenant IDs into unconstrained metric labels. If per-tenant metrics are operationally required, admit tenants through a bounded registry and test the worst-case active-series product.
- Use different scrape intervals according to operational value.

Promotion evidence: measured per-segment ingestion and query budgets, authenticated access tests, and a tenant-isolation test.

## Stage 3 — remote write and distributed storage

- Run collectors or scrape agents close to workloads.
- Queue and remote-write to an evaluated central backend.
- Evaluate Mimir, Thanos, or another candidate against measured tenancy, availability, retention, cost, and query requirements.
- Define backpressure, retry, deduplication, outage buffering, and replay behavior.
- Operate the telemetry backend with its own RED metrics and alerts.

Promotion evidence: failure-injection results, remote-write backlog recovery, duplicate/reorder behavior, availability objectives, tenant isolation, and restore drills.

No distributed backend is selected by this repository. Local Docker success is not production-scale evidence.

