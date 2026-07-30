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

`ReceiptLedger` serializes canonical JSON into an append-only file and chains
each entry to the preceding SHA-256 digest. When
`GDW_RECEIPT_SIGNING_REQUIRED=1`, every new schema-v2 record is signed with a
file-backed ECDSA P-256 key, the trusted public-key fingerprint is checked on
read, and readiness fails if the required key, hash chain, signature sequence,
or signed head is invalid. A signed runtime-activation record anchors an
existing legacy unsigned prefix without rewriting historical bytes. The first
signed record is also bound into a separately signed transition marker beside
the ledger. Once that marker exists, startup refuses a ledger that has been
deleted or truncated back to its unsigned prefix instead of silently creating
a replacement anchor.

The authenticated receipt surface returns the signed head plus its public key
for offline verification. Every decision receipt binds exact source, image,
and deployment mode. Signed backup manifests bind a byte-exact ledger copy,
record count, head, runtime identity, and signing key; the proof profile
restores into isolated tmpfs and verifies the result.

These controls do not supply an independent witness, hardware monotonic
counter, off-host copy, replication, high availability, or multi-node
consensus. File-backed single-node signing and a separate local backup volume
must not be described as those stronger properties.
