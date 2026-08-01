# GDW-X frontier posture

Status: **MODELED**

## What is frontier-aligned now

1. Cognition and execution oversight are separate. The API proposes, simulates, evaluates policy, and records decisions; it has no autonomous actuator.
2. Policy is a versioned data artifact. Every decision receipt binds the evaluated policy revision and digest to a canonical input digest.
3. Caller input cannot weaken required approvals. Unknown actions and missing receipts fail closed.
4. Operational memory is append-only, idempotent, provenance-bearing, temporally aware, and able to represent causal parent links.
5. Counterfactuals expose uncertainty intervals and rank interventions by conservative modeled utility.
6. Calibration includes descriptive scoring and a held-out conformal threshold path that returns `UNAVAILABLE` when evidence is insufficient.
7. Benchmark outputs identify whether they are contract fixtures, supplied-observation metrics, or modeled counterfactuals.

## What still blocks a cutting-edge claim

1. Policy bundles are hashed but not signed, remotely distributed, independently verified, or evaluated by an external policy engine.
2. Local JSONL ledgers have no trusted identity, timestamp, witness, replication, transactional database, retention policy, or disaster recovery.
3. Similarity retrieval is numeric and interpretable but not yet hybrid semantic, graph, temporal, and causal retrieval over a durable index.
4. Intervention effects are hypotheses. A target environment must produce randomized, canary, or defensible quasi-experimental outcome evidence before causal use.
5. Conformal guarantees require held-out exchangeable data. Drift detection, sequential risk control, and recalibration orchestration are not yet integrated.
6. Decoder fixtures are not an external holdout. OpCalEval needs versioned datasets, blinded labels, adversarial multi-fault cases, baseline systems, and reproducible scorecards.
7. There is no capability-secured actuator, rollback controller, human approval service, multi-tenant authorization layer, or scientific/QPU adapter.
8. Agent and tool telemetry does not yet implement the evolving OpenTelemetry GenAI semantic conventions because GDW-X currently performs no model or tool invocation.

## Promotion sequence

1. Replace the local policy evaluator with a signed, versioned policy-bundle adapter while preserving fail-closed local fallback.
2. Add signed, identity-bound decision attestations and an independent witness.
3. Move receipts and incident episodes to a transactional event store with vector and graph indexes.
4. Add shadow-mode intervention collection, drift detection, and sequential risk control.
5. Publish external OpCalEval and QuantCalEval datasets with holdout governance and baseline comparisons.
6. Add a capability-scoped actuator with idempotency, canarying, rollback, and human override.
7. Add the operator console and QPU diagnostics adapter only after the control and evidence layers are measured.

## Primary technical anchors

- NVIDIA QCalEval: https://research.nvidia.com/publication/2026-04_qcaleval-benchmarking-vision-language-models-quantum-calibration-plot
- Runtime Governance for Policy-Constrained Execution: https://arxiv.org/abs/2604.07833
- Anytime-Valid Conformal Risk Control: https://arxiv.org/abs/2602.04364
- Open Policy Agent decision logs and bundles: https://www.openpolicyagent.org/docs/management-decision-logs
- OpenTelemetry GenAI semantic conventions: https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/
