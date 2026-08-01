# GDW-X Master Suite: DASC, Control Isolation, and Telemetry

## Status

- Suite status: `MODELED`
- Regulatory assessment: `NOT_ASSESSED`
- Broker connector: `UNAVAILABLE`
- Canary and live authority: `UNAVAILABLE`
- Paper and shadow acknowledgement path: implemented as a local modeled control-plane transition
- Durable state: append-only bronze JSONL, derived silver snapshot, and gold exemplars

No component in this suite asserts Rule 15c3-5 compliance, broker-dealer
authorization, market-access authorization, a conformal coverage guarantee, or
production readiness.

## Architecture

```text
strategy mutation
  -> DASC-style empirical conformal localization
  -> deterministic replay simulation
  -> independent governance policy
  -> signed, expiring paper/shadow manifest
  -> parent-hash reconciliation
  -> modeled runtime acknowledgement
  -> 15c3-5-style pre-execution risk gate
  -> outcome and exemplar memory
```

Strategy identities have no API or object capability for changing
`RiskControls`. Protected risk parameters force governance review. The risk gate
rejects invalid or expired manifests, parent-hash mismatches, unauthorized
actors, restricted instruments, duplicate intents, order-size and notional
breaches, price-deviation breaches, and all canary/live requests. Even a passing
paper/shadow gate returns `broker_submission=false`.

The architecture is informed by the SEC's direct-and-exclusive-control and
pre-trade rejection requirements, but it is only an engineering analogy:

- SEC Rule 15c3-5 final rule:
  <https://www.sec.gov/files/rules/final/2010/34-63241.pdf>
- SEC market-access FAQ:
  <https://www.sec.gov/rules-regulations/staff-guidance/trading-markets-frequently-asked-questions/divisionsmarketregfaq-0>

## DASC-style localization

`DASCLocalizer` uses per-dimension robust nonconformity scores and empirical
conformal p-values to identify the observed dimensions least consistent with a
reference window. It also derives a drift-adaptive residual interval, evidence
quality, calibrated confidence, an abstention decision, and an alert family.

This is a bounded engineering wrapper, not a reproduction of the research
method. It makes no finite-sample or distribution-free coverage claim under
drift. A reference pool below the configured minimum forces abstention.

Research basis:
<https://arxiv.org/abs/2602.19790>

## Durable lake memory

- `bronze/events.jsonl` is append-only and hash chained. Raw events are never
  overwritten.
- `silver/master_suite_state.json` is a derived current-state projection.
- `gold/exemplars.jsonl` joins the mutation, decision, manifest/ack state, and
  measured or modeled outcome.

Event timestamps are UTC and forced monotonic within the local stream. A broken
event hash, parent hash, or sequence fails the service closed.

The journal defaults to `GDW_DATA_DIR/master_suite`, which places it on the
shipped writable data volume. `GDWX_MASTER_SUITE_DATA_ROOT` remains an explicit
override. The current service keeps its replayed state in process memory, so the
runtime enforces `GDW_WORKERS=1` and fails startup closed for any other value.

## API

- `POST /v1/strategy/mutations`
- `POST /v1/master-suite/calibration/proposals` (Master Suite mutation alias)
- `POST /v1/calibration/proposals` (Calibration Intelligence Plane proposals only)
- `POST /v1/calibration/simulate/{proposal_hash}`
- `POST /v1/governance/decisions/{proposal_hash}`
- `POST /v1/runtime/manifests`
- `POST /v1/runtime/acks`
- `POST /v1/risk-gate/evaluate`
- `POST /v1/outcomes`
- `GET /v1/dashboard/state`
- `GET /v1/dashboard/alerts`
- `GET /v1/lake/exemplars`
- `GET /master-suite`

Identifiers belong in event records and spans, not Prometheus labels. The API
creates bounded span attributes for service role, formula, deployment mode, and
decision state. Application-level FastAPI instrumentation is registered through
`opentelemetry-instrumentation-fastapi`; if that package is absent, the app
reports telemetry as `UNAVAILABLE` instead of claiming instrumentation.

OpenTelemetry reference:
<https://opentelemetry.io/docs/zero-code/python/>

## Required environment

`GDWX_MASTER_SUITE_SIGNING_KEY` must be provided through the runtime environment
or an approved secret store before the governance kernel can accept proposals
or issue manifests. No default signing secret is embedded. The runtime also
requires `GDW_WORKERS=1` while workflow state remains process-local. Optional
settings:

- `GDWX_MASTER_SUITE_SIGNER`
- `GDWX_MASTER_SUITE_DATA_ROOT` (overrides `GDW_DATA_DIR/master_suite`)
- `GDWX_ACTIVE_RUNTIME_HASH`

The signing key is HMAC-based local modeling. It is not independent public-key
approval infrastructure and must not be represented as such.

## Acceptance boundary

- Direct strategy-to-runtime mutation: structurally blocked.
- Evidence, simulation, governance decision: required before manifest issuance.
- Invalid signatures, expiry, and parent mismatch: fail closed.
- Conformal outputs: attached to proposals, alerts, and dashboard state.
- Mutation-to-outcome join: persisted through bronze, silver, and gold zones.
- OTel instrumentation: implemented, but operational exporter delivery remains
  unverified until run against a configured collector.
- Scorecard: implemented as a read-only live-state API and responsive console.
- Production execution and compliance: unavailable and unassessed.
