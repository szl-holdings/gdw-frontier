# GDW Self-Defending Observability Core

Status: **MODELED**

This repository turns the Wave 34 observability payload into a runnable, governed reference system. It couples a small deterministic FastAPI surface to Prometheus, Grafana, Loki, Grafana Alloy, Tempo, Terraform, alert rules, cardinality policy, artifact-weight locks, worker sizing, Postman smoke tests, and a full local signal-path verifier.

> GDW Self-Defending Observability Core is a MODELED operational layer for the Governed Delta Workspace. It adds structured logging, Prometheus metrics, Grafana/Loki dashboards, alerting, Terraform provisioning, cardinality audits, and trace-ready hooks. It does not claim universal production-scale guarantees or frontier benchmark superiority beyond measured outputs and recorded validation artifacts.

The block above is the required Wave 34 disclosure and must remain verbatim.

## What moved beyond the draft

- Metrics use bounded route templates and enumerated labels. Request, session, proposal, trace, span, UUID, and hash values are banned as Prometheus labels.
- The cardinality gate uses Python AST inspection instead of line heuristics, checks the live OpenMetrics exposition, enforces label-value allowlists and per-metric/total series budgets, and can compare against a reviewed baseline.
- OpenTelemetry is active now: server spans export over OTLP/HTTP to Tempo when an endpoint is configured. Trace IDs appear in structured logs and OpenMetrics exemplars.
- Grafana data sources, the 15-panel live-operations dashboard, and four Grafana-managed alert rules are reproducible through Terraform.
- Grafana Alloy replaces Promtail. Promtail reached end of life and was removed from current Loki releases, so a new deployment should not depend on it.
- Error-ratio queries use a defended denominator. Alerts include telemetry absence, not only service symptoms.
- Local receipts form an append-only SHA-256 chain and are described honestly as local and unsigned.
- A one-command verifier proves the local metrics, logs, traces, dashboard, and alert paths before writing a `VERIFIED` integration artifact.

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
python ci/worker_calc.py
docker compose -f monitoring/docker-compose.yml up -d --build
python ci/verify_stack.py
python ci/audit_cardinality.py \
  --metrics-url http://localhost:8080/metrics \
  --baseline weights/cardinality-baseline.json
```

Open:

- API docs: `http://localhost:8080/docs`
- Grafana: `http://localhost:3000/d/gdw-live-ops` (anonymous viewer is enabled for this local stack)
- Prometheus: `http://localhost:9090`
- Alloy: `http://localhost:12345`

Stop and remove local volumes:

```bash
docker compose -f monitoring/docker-compose.yml down --volumes
```

That command removes only this Compose project’s local telemetry and Terraform state volumes.

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

- a missing local receipt fails closed to `withhold`;
- otherwise confidence at least `0.85` maps to `allow`;
- confidence at least `0.60` maps to `review`;
- lower confidence maps to `withhold`.

This policy exists to exercise observable branches. It is not a universal trust model, benchmark, or production authorization rule. Raw session and proposal identifiers never enter metric labels; logs contain a one-way, truncated session reference.

## Acceptance evidence

The repository distinguishes static validation from live integration:

| Gate | Evidence written by | Truth boundary |
|---|---|---|
| Python/API/unit behavior | `pytest -q` | Local process only |
| Static label governance | `ci/audit_cardinality.py` | Source and checked-in policy |
| Live series budgets | `ci/audit_cardinality.py --metrics-url ...` | One observed scrape |
| Dashboard/rule/artifact integrity | `ci/gdw_gate.py` | Checked-in source |
| Prometheus/Loki/Tempo/Grafana path | `ci/verify_stack.py` | Local Docker Compose only |
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

These results verify this local run only. Production scale, high availability, tenant isolation, cryptographic receipt signing, and target-environment readiness remain **UNVERIFIED**.

## Scale posture

The local stack is single-instance and deliberately short-retention. The promotion ladder is:

1. keep label cardinality bounded and enforce sample/label limits;
2. separate production, bench, and tenant scrape jobs with explicit budgets;
3. move collection toward agents and remote write;
4. choose a multi-tenant backend such as Mimir, Thanos, or another evaluated store only after measured query, ingestion, retention, availability, and isolation requirements exist.

Storage is not a substitute for fixing unbounded labels. See [docs/SCALING.md](docs/SCALING.md).

## Security boundaries

- The local Grafana password is intentionally development-only and can be overridden with `GRAFANA_ADMIN_PASSWORD`.
- Do not expose this Compose stack directly to an untrusted network.
- Terraform credentials are passed through variables and are not committed.
- Loki only indexes the bounded `job`, `service`, and `level` labels; request/session correlation remains structured log content.
- The local receipt chain detects mutation but is not signed, independently witnessed, or durable across volume deletion.

## Primary references

- [Prometheus instrumentation practices](https://prometheus.io/docs/practices/instrumentation/)
- [OpenTelemetry Python exporters](https://opentelemetry.io/docs/languages/python/exporters/)
- [Grafana Terraform provider](https://grafana.com/docs/grafana/latest/as-code/infrastructure-as-code/terraform/)
- [Grafana alerting with Terraform](https://grafana.com/docs/grafana/latest/alerting/set-up/provision-alerting-resources/terraform-provisioning/)
- [Grafana Tempo signal correlations](https://grafana.com/docs/grafana/latest/datasources/tempo/configure-tempo-data-source/)
- [Loki release notes documenting Promtail removal](https://grafana.com/docs/loki/latest/release-notes/v3-7/)
