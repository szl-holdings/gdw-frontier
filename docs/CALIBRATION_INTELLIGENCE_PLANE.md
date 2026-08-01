# SZL Calibration Intelligence Plane

Status: **MODELED**

## Rule zero

The quant engine may propose. It may never authorize its own deployment, protected risk limits, or orders.

The implemented authority ladder is:

```text
offline -> simulated -> approved for paper or shadow -> authenticated manifest -> runtime ack
```

There is no canary, live, broker, venue, or order-submission adapter. Requests for canary or live authority route to review and cannot receive a manifest.

## Implemented modules

- Stable Pydantic contracts and canonical proposal hashes.
- Six-dimension drift taxonomy and constructed runtime confidence.
- Four deterministic replay scenarios: abrupt shift, gradual drift, recurring regime, and telemetry loss.
- Independent governance policy blocking protected risk parameters.
- HMAC-authenticated, expiring paper/shadow manifests.
- Parent-hash reconciliation, idempotent runtime acknowledgements, and fail-closed mismatches.
- Bronze, silver, and gold append-only calibration episodes with regime-conditioned retrieval.
- Bounded Prometheus metrics without formula, proposal, runtime-hash, symbol, or request identity labels.
- A read-only calibration console at `/calibration`.

## Signature boundary

The local authenticator uses HMAC-SHA256. If `GDWX_MANIFEST_SIGNING_KEY` is absent, the process creates an ephemeral key and reports `EPHEMERAL_SIGNING_KEY`. This proves local functional authentication only. It is not public-key signing, independent approval, durable key custody, a transparency log, or a production release receipt.

## Research and regulatory boundary

NVIDIA Ising provides a relevant pattern for diagnostic interpretation, related-example retrieval, fit assessment, and next-step recommendation. Its QCalEval and quantum-calibration results do not establish trading performance or economic edge:

- https://developer.nvidia.com/blog/nvidia-ising-enables-fully-automated-quantum-computer-calibration-with-enhanced-in-context-learning/
- https://research.nvidia.com/publication/2026-04_qcaleval-benchmarking-vision-language-models-quantum-calibration-plot

Drift-aware spectral conformal prediction motivates regime-conditioned residual retrieval, explicit drift diagnostics, and effective-sample support. Its guarantees depend on stated assumptions and do not transfer automatically to this implementation:

- https://arxiv.org/html/2606.15953

SEC Rule 15c3-5 requires reasonably designed pre-trade controls and direct broker-dealer control of market-access risk systems. GDW-X is not a broker-dealer control system and does not claim compliance:

- https://www.sec.gov/files/rules/final/2010/34-63241.pdf

## Remaining promotion gates

1. Public-key signatures with independently controlled identities and durable key custody.
2. Transactional event storage, replication, backup, and cross-restart state recovery.
3. Real streaming conformal calibration with drift diagnostics and effective-sample checks.
4. External QuantCalEval datasets, blinded holdouts, baselines, and adversarial cases.
5. Measured paper and shadow runs with delayed outcomes and simulator-to-observed error.
6. Independent legal, compliance, broker, venue, and risk review before any market authority.
