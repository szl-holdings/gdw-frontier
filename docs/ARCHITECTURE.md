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
or signed head is invalid. Initializing a new signed ledger or migrating a
non-empty legacy unsigned prefix requires
`GDW_ALLOW_UNSIGNED_LEDGER_MIGRATION=1` for one startup. That startup writes a
signed runtime-activation boundary and separate signed continuity state
without rewriting historical bytes. Subsequent loss of the recorded state
fails closed instead of creating a replacement anchor.
The live verifier refuses to certify a deployment until the one-start
authorization is returned to `0`.

The authenticated receipt surface returns the signed head plus its public key
for offline verification. Every decision receipt binds exact source, image,
and deployment mode. Signed backup manifests bind a byte-exact ledger copy,
its signing continuity state, record count, head, runtime identity, and signing
key; the proof profile restores into isolated tmpfs and verifies the result.

These controls do not supply an independent witness, hardware monotonic
counter, off-host copy, replication, high availability, or multi-node
consensus. File-backed single-node signing and a separate local backup volume
must not be described as those stronger properties.

## GDW-X control-plane boundary

Wave 37 layers seven modules over the existing signal and receipt paths:

```text
typed diagnostics
  -> interpretable Quant Core
  -> Calibration Plane
  -> Anomaly Decoder
  -> Counterfactual Simulator
  -> Governance Kernel
  -> local audit receipt
       ^
       |
append-only exemplar memory -> similarity retrieval
```

Calibration and simulation are pure proposal paths. They do not execute
mutations. `POST /v1/gdw/policy/check`, rejected `POST /v1/gdw/step` calls,
threshold proposals, and exemplar-record decisions are appended to the governed
receipt chain so rejections remain inspectable. Under the production signing
profile, these records inherit the file-backed ECDSA signing and continuity
checks described above.

The exemplar vault uses a separate append-only hash chain. Readiness verifies
both chains, but the exemplar chain is not independently signed, replicated,
durable across volume deletion, or suitable as production release proof.

The fixed decoder cases and `OpCalEval`/`QuantCalEval` functions establish benchmark interfaces. Their checked-in examples are MODELED regression fixtures, not evidence of benchmark superiority.

## Policy provenance and uncertainty

The application loads one fail-closed `gdw.policy.v1` bundle. Policy evaluation takes the union of bundle-required and caller-requested approval, so input can strengthen but cannot weaken governance. Every decision envelope contains the policy revision, policy digest, canonical subject digest, decision, and reasons.

The envelope is inspired by modern attestation subject-digest patterns, but it is neither signed nor claimed to implement in-toto or SLSA. External verification, identity binding, trusted timestamps, transparency logging, and independent witnessing remain `UNVERIFIED`.

Simulation returns an interval generated by a versioned heuristic effect model. `OpCalEval` ranks by conservative improvement. No causal effect is `MEASURED` until target-environment interventions provide held-out outcome evidence.

Conformal risk control emits a threshold only when the caller supplies the configured minimum number of held-out nonconformity scores. Its finite-sample interpretation requires exchangeability and must be invalidated under detected drift.
