# Architecture and governance

## Trust boundaries

The application emits three signals from one request context:

1. a Prometheus histogram observation with a trace exemplar;
2. structured JSON log records with trace/span identifiers;
3. an OpenTelemetry span exported over OTLP/HTTP.

Identity-bearing values remain outside Prometheus labels. The session ID is hashed to a 16-character reference for bounded diagnostic lookup in logs. The proposal identifier is stored only as a full SHA-256 digest in the local receipt body. This reduces accidental plaintext exposure; it does not make low-entropy identifiers anonymous.

## Metric governance

`monitoring/metric-policy.json` is the canonical label contract. `ci/audit_cardinality.py`:

- parses metric constructors from the Python AST;
- rejects dynamic metric/label declarations;
- rejects banned identity-like labels;
- requires each `gdw_` metric to match the registered label contract;
- parses a live OpenMetrics scrape;
- rejects unexpected enumerated values;
- enforces per-metric and total sample-series budgets;
- optionally rejects growth beyond a reviewed baseline.

Prometheus independently enforces scrape-side sample, label-count, label-name-length, and label-value-length limits. The two layers defend different failure modes.

## Provisioning ownership

Terraform is the canonical owner in `monitoring/docker-compose.yml`. It provisions:

- Prometheus, Loki, and Tempo data sources;
- cross-signal links;
- the stable `gdw-observability` folder;
- the stable `gdw-live-ops` dashboard;
- four Grafana-managed alert rules.

File provisioning YAML is an alternate deployment mechanism. Enabling both against one Grafana instance creates competing ownership and is unsupported.

## Receipt boundary

`ReceiptLedger` serializes canonical JSON into an append-only file and chains each entry to the preceding SHA-256 digest. Readiness fails if that chain does not verify. The ledger has no signature, external witness, monotonic hardware counter, replication, or backup. It is a tamper-evident local development primitive, not a release receipt.

