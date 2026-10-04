# GDW Self-Defending Observability Core

Status: **MODELED**

This repository turns the Wave 34 observability payload into a runnable, governed reference system. It couples a small deterministic FastAPI surface to Prometheus, Grafana, Loki, Grafana Alloy, Tempo, Terraform, alert rules, cardinality policy, artifact-weight locks, worker sizing, Postman smoke tests, and a full local signal-path verifier.

> GDW Self-Defending Observability Core is a MODELED operational layer for the Governed Delta Workspace. It adds structured logging, Prometheus metrics, Grafana/Loki dashboards, alerting, Terraform provisioning, cardinality audits, and trace-ready hooks. It does not claim universal production-scale guarantees or frontier benchmark superiority beyond measured outputs and recorded validation artifacts.

The block above is the required Wave 34 disclosure and must remain verbatim.

## Wave 37: GDW-X modular control plane

> GDW-X is a MODELED modular control-plane architecture for governed operational intelligence. It combines quant scoring, calibration, long-context incident memory, anomaly decoding, simulation, and policy-constrained execution. Inspired by NVIDIA Ising, Kimi K3, and runtime-governance research, it does not claim autonomous production safety or benchmark superiority beyond measured results and enforced controls.

The block above is the required Wave 37 disclosure and must remain verbatim.

Wave 37 extends the existing observability core instead of creating a parallel runtime:

- Quant Core exposes monotonic, editable, non-negative scoring factors and a complete penalty breakdown.
- Calibration Plane converts a typed diagnostic snapshot into reversible action proposals.
- Governance Kernel separates proposals from execution, requires valid input receipts, routes approval-gated actions to review, and writes local audit receipts for allow, review, and withhold outcomes.
- Long-Context Ops Memory stores incident snapshots, interventions, and outcomes in a separate append-only local hash chain and ranks retrieved exemplars by scaled similarity, trust, and risk.
- Anomaly Decoder classifies receipt, artifact-weight, error-rate, latency, and cardinality failures.
- Simulation and benchmark modules expose counterfactual release-score deltas, `OpCalEval`, `QuantCalEval`, and a fixed decoder benchmark.

These modules are deterministic reference implementations. Calibration outputs and simulated deltas are proposals, not authority to mutate production systems.

### Frontier posture

Version `0.3.0` adds a policy-provenance and uncertainty boundary:

- machine-readable policy is loaded from `app/policies/gdw-policy-bundle.json`;
- callers cannot downgrade approval requirements encoded in that bundle;
- every governance receipt binds the exact policy revision and SHA-256 digest to a canonical subject digest;
- incident writes are idempotent and can carry temporal, source, tag, and causal-parent metadata;
- counterfactuals expose conservative, central, and optimistic projections instead of a misleading point estimate;
- conformal risk control refuses to emit an operating threshold when the supplied held-out sample is too small;
- built-in decoder cases are labeled contract fixtures, not external benchmark evidence.

This is a stronger control-plane foundation, not proof that GDW-X is state of the art. See `docs/FRONTIER_POSTURE.md` for the measured frontier and remaining gates.

## SZL Calibration Intelligence Plane

The modular `gdw_x` package implements the payload's paper/shadow control loop without adding market authority:

```text
formula proposal
  -> deterministic replay
  -> independent kernel decision
  -> expiring authenticated manifest
  -> parent-hash reconciliation
  -> paper/shadow runtime acknowledgement
  -> lake-backed outcome episode
```

The read-only operator console is available at `http://localhost:8080/calibration`. Canary, live trading, broker connectivity, protected risk-parameter tuning, and order submission are structurally unavailable. See `docs/CALIBRATION_INTELLIGENCE_PLANE.md`.

## What moved beyond the draft

- Metrics use bounded route templates and enumerated labels. Request, session, proposal, trace, span, UUID, and hash values are banned as Prometheus labels.
- The cardinality gate uses Python AST inspection instead of line heuristics, checks the live OpenMetrics exposition, enforces label-value allowlists and per-metric/total series budgets, and can compare against a reviewed baseline.
- OpenTelemetry is active now: server spans export over OTLP/HTTP to Tempo when an endpoint is configured. Trace IDs appear in structured logs and OpenMetrics exemplars.
- Grafana data sources, the 15-panel live-operations dashboard, and four Grafana-managed alert rules are reproducible through Terraform.
- Grafana Alloy replaces Promtail. Promtail reached end of life and was removed from current Loki releases, so a new deployment should not depend on it.
- Error-ratio queries use a defended denominator. Alerts include telemetry absence, not only service symptoms.
- Receipts can use a durable file-backed ECDSA P-256 signing root. Explicit one-start approval initializes a new signed ledger or anchors a legacy unsigned prefix and writes signed continuity state; later total state loss fails closed instead of silently reinitializing.
- Signed backup manifests bind the ledger digest, signed continuity state, record count, head hash, runtime identity, and signing key. The proof profile restores the latest backup into isolated tmpfs before reporting `VERIFIED`.
- A bounded authenticated load harness measures successful writes, throughput, latency percentiles, unique receipt hashes, and the exact signed-ledger delta.
- A one-command verifier proves the local metrics, logs, traces, dashboard, and alert paths before writing a `VERIFIED` integration artifact.
- The single-node live profile binds every published port to loopback, disables anonymous Grafana access, requires a strong bearer token for operator endpoints, publishes exact source/image identity, and can verify receipt-ledger recovery across a container restart.

## New Frontier operating doctrine

The [New Frontier operating playbook](docs/FRONTIER_PLAYBOOK.md) turns current
leadership, design-thinking, circular-fashion, systems, reliability, and AI
risk-management patterns into an original seven-stage loop:

`Sense -> Frame -> Diverge -> Prototype -> Prove -> Circulate -> Govern`

It adds an explicit originality boundary: learn from public methods, but do not
copy another brand, designer, implementation, or distinctive expression.

## Signal path

```text
FastAPI
  ├─ OpenMetrics + exemplars ───────────────> Prometheus ──> Grafana
  ├─ structured JSON file logs ──> Alloy ──> Loki ────────> Grafana
  └─ OpenTelemetry OTLP/HTTP spans ─────────> Tempo ──────> Grafana
                                                ▲
                     trace_id joins metrics, logs, and traces
```

The Terraform container waits for Grafana, then applies the version-controlled data sources, dashboard, folder, and alert rule group. The alternate file-provisioning YAML is retained under `monitoring/grafana/provisioning/` for operators who do not use Terraform; do not enable both ownership paths against the same Grafana instance.

## Quick start

Requirements: Docker Desktop with Compose v2. The stack binds ports `3000`, `3100`, `3200`, `4317`, `4318`, `8080`, `9090`, and `12345` on localhost.

```bash
openssl rand -base64 -out /secure/gdw-api-token 48
openssl rand -base64 -out /secure/gdw-grafana-password 48
openssl rand -base64 -out /secure/gdw-master-suite-signing-key 48
python ci/generate_signing_key.py --output /secure/gdw-receipt-signing-key.pem
export GDW_API_TOKEN_SOURCE_FILE="/secure/gdw-api-token"
export GRAFANA_ADMIN_PASSWORD_SOURCE_FILE="/secure/gdw-grafana-password"
export GDW_SIGNING_KEY_SOURCE_FILE="/secure/gdw-receipt-signing-key.pem"
export GDWX_MASTER_SUITE_SIGNING_KEY_SOURCE_FILE="/secure/gdw-master-suite-signing-key"
export GDW_RECEIPT_SIGNING_REQUIRED=1
export GDW_ALLOW_UNSIGNED_LEDGER_MIGRATION=1
python ci/worker_calc.py
docker compose -f monitoring/docker-compose.yml up -d --build
python ci/verify_stack.py
export GDW_ALLOW_UNSIGNED_LEDGER_MIGRATION=0
docker compose -f monitoring/docker-compose.yml up -d --no-build --force-recreate app
python ci/audit_cardinality.py \
  --metrics-url http://localhost:8080/metrics \
  --baseline weights/cardinality-baseline.json
```

The Master Suite HMAC secret must contain at least 32 UTF-8 bytes. The worker
calculator reports `workers: 1`, the only supported runtime setting while
workflow state is process-local; its CPU and memory fields are capacity
observations, not permission to start additional workers or replicas. Direct
ASGI launches must keep lifespan enabled because the governed startup audit and
exclusive process lease run there. CLI or `UVICORN_LIFESPAN=off` configuration
fails at import, and a programmatic server that suppresses lifespan can serve
only `503 governed runtime startup incomplete` responses.

Open:

- API docs: `http://localhost:8080/docs`
- Grafana: `http://localhost:3000/d/gdw-live-ops` (sign in as `admin` with the configured local password)
- Prometheus: `http://localhost:9090`
- Alloy: `http://localhost:12345`

Stop the local stack while preserving its containers and persistent volumes:

```bash
docker compose -f monitoring/docker-compose.yml stop
```

The Compose file sets the project name to `gdw`. Its named volumes include receipt
data (`gdw-data`) and backups (`gdw-backups`), as well as logs, telemetry, Grafana,
and Terraform state. `down --volumes` deletes these volumes; it is not a routine
stop command. Never use it against the default `gdw` project or production volumes.
Destructive cleanup is only appropriate for a separately isolated, explicitly
disposable test project whose volumes contain no evidence or backups to retain.
If the stack was started with a project-name override, use that same override when
stopping it; changing directories alone does not isolate the fixed `gdw` project.

For a credential-backed HTTPS deployment on one Docker host, follow
[`docs/SINGLE_NODE_LIVE.md`](docs/SINGLE_NODE_LIVE.md). The live verifier
records exact source and image identity, fail-closed operator authentication,
and receipt-ledger recovery across an application-container restart. This is
stronger than a local smoke test, but it is still not high-availability or
multi-node production proof.

## Development checks

```bash
python -m pip install -e ".[test]"
pytest -q
python ci/update_weight_manifest.py --check
python ci/gdw_gate.py --with-docker
docker run --rm \
  -v "$PWD/monitoring:/etc/prometheus:ro" \
  prom/prometheus:v3.13.1 \
  promtool check config /etc/prometheus/prometheus.yml
terraform -chdir=terraform fmt -check
terraform -chdir=terraform validate
```

The weight updater is deliberately not a CI auto-fix. When a governed artifact intentionally changes, inspect the diff and run:

```bash
python ci/update_weight_manifest.py --write
python ci/validate_weights.py
```

## API behavior

`POST /v1/gdw/step` exercises a minimal deterministic **MODELED** policy:

- a missing input receipt fails closed to `withhold` and the rejection receives a local audit receipt;
- otherwise confidence at least `0.85` maps to `allow`;
- confidence at least `0.60` maps to `review`;
- lower confidence maps to `withhold`.

This policy exists to exercise observable branches. It is not a universal trust model, benchmark, or production authorization rule. Raw session and proposal identifiers never enter metric labels; logs contain a one-way, truncated session reference.

The GDW-X product surface adds:

```text
POST /v1/gdw/calibrate
POST /v1/gdw/decode
POST /v1/gdw/simulate
GET  /v1/gdw/score/release
GET  /v1/gdw/exemplars/search
POST /v1/gdw/exemplars
POST /v1/gdw/policy/check
POST /v1/gdw/quant/calibrate
POST /v1/gdw/quant/risk-control
GET  /v1/gdw/benchmarks/decoder
POST /v1/gdw/benchmarks/opcal
```

There is intentionally no autonomous execution endpoint. The policy surface evaluates and receipts proposals; a separately authorized actuator would be required for real mutations.

## Acceptance evidence

The repository distinguishes static validation from live integration:

| Gate | Evidence written by | Truth boundary |
|---|---|---|
| Python/API/unit behavior | `pytest -q` | Local process only |
| Static label governance | `ci/audit_cardinality.py` | Source and checked-in policy |
| Live series budgets | `ci/audit_cardinality.py --metrics-url ...` | One observed scrape |
| Dashboard/rule/artifact integrity | `ci/gdw_gate.py` | Checked-in source |
| Prometheus/Loki/Tempo/Grafana path | `ci/verify_stack.py` | Local Docker Compose only |
| Credentialed HTTPS ingress, signed receipts, backup/restore, and restart recovery | `ci/verify_live_deployment.py` | One observed single-node deployment |
| Independent public identity and signed-head witness | `ci/verify_remote_witness.py` | One read-only external observation; no HA or disaster-recovery claim |
| Bounded authenticated write load | `ci/measure_load.py` | One target, request count, and concurrency setting |
| Production scale, HA, tenant isolation | Not produced here | `UNVERIFIED` until measured in the target environment |

Generated reports live under `output/` and are ignored by Git. CI uploads them as run-scoped artifacts.

### Observed local verification — 2026-07-29

The controlled two-worker Compose run produced the following **MEASURED local** evidence:

- 14 Python/API and manifest-portability tests passed;
- three Newman API assertions passed;
- 15 Grafana panels rendered with no browser-console errors;
- four Prometheus rules and four Terraform-managed Grafana alert rules were healthy;
- seven decisions were visible through the aggregate multiprocess metrics endpoint;
- 53 bounded GDW series were observed with no cardinality-policy findings;
- one request was correlated across the API receipt, Loki log stream, and Tempo trace.

These dated results verify that local run only. A later deployment must produce
fresh signed-receipt, backup/restore, and load artifacts. Production scale,
high availability, multi-node durability, tenant isolation, off-host disaster
recovery, and independent receipt witnessing remain **UNVERIFIED**.

## Scale posture

The local stack is single-instance and deliberately short-retention. The promotion ladder is:

1. keep label cardinality bounded and enforce sample/label limits;
2. separate production, bench, and tenant scrape jobs with explicit budgets;
3. move collection toward agents and remote write;
4. choose a multi-tenant backend such as Mimir, Thanos, or another evaluated store only after measured query, ingestion, retention, availability, and isolation requirements exist.

Storage is not a substitute for fixing unbounded labels. See [docs/SCALING.md](docs/SCALING.md).

## Security boundaries

- The local Grafana password is intentionally development-only and is sourced from `GRAFANA_ADMIN_PASSWORD_SOURCE_FILE`.
- Compose mounts the GDW token and Grafana password as files; it does not place either secret in container environment metadata.
- Compose also mounts the P-256 private signing key as a file. The key generator refuses to overwrite an existing key, and live signing fails closed when the configured key is missing or invalid.
- A one-shot password-sync container applies the mounted Grafana secret to both new and existing local Grafana databases before Terraform provisioning runs.
- All published Compose ports bind to `127.0.0.1` unless `GDW_BIND_ADDRESS` is deliberately overridden.
- Anonymous Grafana access is disabled by default.
- When `GDW_REQUIRE_AUTH=1`, operator endpoints require a bearer token of at least 32 characters; health, readiness, build identity, and the internally scraped metrics endpoint remain unauthenticated.
- Do not expose the stack directly to an untrusted network. The supplied tunnel example denies public `/metrics` access and leaves operator endpoints behind bearer authentication.
- Terraform credentials are passed through variables and are not committed.
- Loki only indexes the bounded `job`, `service`, and `level` labels; request/session correlation remains structured log content.
- Signed receipt heads and signed backup manifests are locally verifiable. They are not independently witnessed, backed by a hardware monotonic counter, replicated across nodes, or sufficient proof of off-host disaster recovery.

## Primary references

- [Prometheus instrumentation practices](https://prometheus.io/docs/practices/instrumentation/)
- [OpenTelemetry Python exporters](https://opentelemetry.io/docs/languages/python/exporters/)
- [Grafana Terraform provider](https://grafana.com/docs/grafana/latest/as-code/infrastructure-as-code/terraform/)
- [Grafana alerting with Terraform](https://grafana.com/docs/grafana/latest/alerting/set-up/provision-alerting-resources/terraform-provisioning/)
- [Grafana Tempo signal correlations](https://grafana.com/docs/grafana/latest/datasources/tempo/configure-tempo-data-source/)
- [Loki release notes documenting Promtail removal](https://grafana.com/docs/loki/latest/release-notes/v3-7/)
