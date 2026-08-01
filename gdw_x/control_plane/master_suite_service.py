from __future__ import annotations

import hmac
import os
from datetime import timedelta
from hashlib import sha256
from pathlib import Path
from threading import RLock
from typing import Any

from gdw_x.data_plane.risk_gate_15c35 import RiskControls, RiskGate15c35
from gdw_x.domain.dasc import DASCLocalizer
from gdw_x.domain.master_suite import (
    DeploymentMode,
    FormulaObservation,
    GovernanceDecision,
    GovernanceDisposition,
    OrderIntent,
    OutcomeObservation,
    RiskGateDecision,
    RuntimeAck,
    RuntimeManifest,
    StrategyMutation,
    canonical_hash,
    canonical_json,
    utc_now,
)
from gdw_x.memory.lake_journal import JournalIntegrityError, LakeJournal


class MasterSuiteError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class MasterSuiteService:
    POLICY_VERSION = "gdw-x-master-suite-2026-07-29"
    PROTECTED_PARAMETERS = frozenset(
        {
            "max_order_size",
            "max_notional",
            "max_price_deviation",
            "credit_limit",
            "capital_limit",
            "kill_switch",
            "restricted_instruments",
        }
    )
    AUTO_MODES = frozenset({DeploymentMode.PAPER, DeploymentMode.SHADOW})

    def __init__(
        self,
        *,
        data_root: Path | None = None,
        signing_key: str | None = None,
    ) -> None:
        project_root = Path(__file__).resolve().parents[2]
        shared_data_root = Path(
            os.getenv("GDW_DATA_DIR", str(project_root / "data"))
        )
        configured_data_root = os.getenv("GDWX_MASTER_SUITE_DATA_ROOT")
        self._journal = LakeJournal(
            data_root
            or (
                Path(configured_data_root)
                if configured_data_root
                else shared_data_root / "master_suite"
            )
        )
        self._signing_key = (
            signing_key or os.getenv("GDWX_MASTER_SUITE_SIGNING_KEY")
        )
        self._signer = os.getenv(
            "GDWX_MASTER_SUITE_SIGNER",
            "local-governance-kernel",
        )
        self._active_runtime_hash = os.getenv(
            "GDWX_ACTIVE_RUNTIME_HASH",
            canonical_hash({"runtime": "gdw-x", "version": "0.3.0"}),
        )
        self._localizer = DASCLocalizer()
        self._risk_gate = RiskGate15c35(RiskControls())
        self._lock = RLock()
        self._integrity_status = "VERIFIED"
        self._mutations: dict[str, dict[str, Any]] = {}
        self._simulations: dict[str, dict[str, Any]] = {}
        self._decisions: dict[str, dict[str, Any]] = {}
        self._manifests: dict[str, dict[str, Any]] = {}
        self._acks: dict[str, dict[str, Any]] = {}
        self._outcomes: dict[str, dict[str, Any]] = {}
        self._risk_decisions: list[dict[str, Any]] = []
        self._alerts: list[dict[str, Any]] = []
        try:
            for event in self._journal.read_events():
                self._apply_event(event)
        except JournalIntegrityError:
            self._integrity_status = "FAILED_CLOSED"

    def _ensure_integrity(self) -> None:
        if self._integrity_status != "VERIFIED":
            raise MasterSuiteError(
                "journal_integrity_failed",
                "The append-only event chain failed verification.",
            )

    def _sign(self, payload: dict[str, Any]) -> str:
        if not self._signing_key:
            raise MasterSuiteError(
                "signing_key_unavailable",
                "GDWX_MASTER_SUITE_SIGNING_KEY is required for signed decisions.",
            )
        return hmac.new(
            self._signing_key.encode("utf-8"),
            canonical_json(payload),
            sha256,
        ).hexdigest()

    def _verify_signature(
        self,
        payload: dict[str, Any],
        signature: str,
    ) -> bool:
        if not self._signing_key:
            return False
        expected = hmac.new(
            self._signing_key.encode("utf-8"),
            canonical_json(payload),
            sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    def _record(
        self,
        *,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        payload: dict[str, Any],
        correlation_id: str,
    ) -> dict[str, Any]:
        self._ensure_integrity()
        event = self._journal.append(
            event_type=event_type,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            payload=payload,
            correlation_id=correlation_id,
        )
        self._apply_event(event)
        self._journal.write_silver_snapshot(self._snapshot())
        return event

    def _apply_event(self, event: dict[str, Any]) -> None:
        event_type = event["event_type"]
        aggregate_id = event["aggregate_id"]
        payload = event["payload"]
        if event_type == "StrategyMutationRequested":
            self._mutations[aggregate_id] = payload
        elif event_type == "SimulationCompleted":
            self._simulations[aggregate_id] = payload
        elif event_type == "GovernanceBridgeDecision":
            self._decisions[aggregate_id] = payload
        elif event_type == "RuntimeManifestIssued":
            self._manifests[payload["manifest_hash"]] = payload
        elif event_type == "RuntimeAck":
            key = f"{payload['manifest_hash']}:{payload['runtime_instance']}"
            self._acks[key] = payload
            if payload["status"] == "applied":
                self._active_runtime_hash = payload["active_runtime_hash"]
        elif event_type == "OutcomeObserved":
            self._outcomes[aggregate_id] = payload
        elif event_type == "RiskGateEvaluated":
            self._risk_decisions.append(payload)
            self._risk_decisions = self._risk_decisions[-500:]
        elif event_type == "RuntimeAlertRaised":
            self._alerts.append(payload)
            self._alerts = self._alerts[-200:]

    def _snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": "2026-07-29",
            "truth_status": "MODELED",
            "active_runtime_hash": self._active_runtime_hash,
            "mutations": self._mutations,
            "simulations": self._simulations,
            "decisions": self._decisions,
            "manifests": self._manifests,
            "acks": self._acks,
            "outcomes": self._outcomes,
            "risk_decisions": self._risk_decisions,
            "alerts": self._alerts,
            "journal": self._journal.integrity_head,
        }

    def submit_mutation(
        self,
        mutation: StrategyMutation,
        observation: FormulaObservation,
        reference_pool: list[FormulaObservation],
        *,
        correlation_id: str,
    ) -> dict[str, Any]:
        with self._lock:
            localization = self._localizer.localize(
                observation,
                reference_pool,
            )
            governed_mutation = mutation.model_copy(
                update={
                    "drift_localization": localization,
                    "confidence_delta": (
                        localization.calibrated_confidence
                        - observation.raw_confidence
                    ),
                    "simulation_summary": {},
                }
            )
            proposal_hash = governed_mutation.proposal_hash()
            if proposal_hash in self._mutations:
                return {
                    "proposal_hash": proposal_hash,
                    "mutation": self._mutations[proposal_hash],
                    "idempotent": True,
                }
            self._record(
                event_type="StrategyMutationRequested",
                aggregate_type="calibration_proposal",
                aggregate_id=proposal_hash,
                payload=governed_mutation.model_dump(mode="json"),
                correlation_id=correlation_id,
            )
            self._record(
                event_type="CalibrationAssessmentCompleted",
                aggregate_type="calibration_proposal",
                aggregate_id=proposal_hash,
                payload=localization.model_dump(mode="json"),
                correlation_id=correlation_id,
            )
            if localization.alert_family != "none":
                self._raise_alert(
                    proposal_hash=proposal_hash,
                    family=localization.alert_family,
                    severity=self._localization_severity(localization.drift.maximum),
                    recommended_next_step=localization.recommended_next_step,
                    correlation_id=correlation_id,
                    context={
                        "formula_id": mutation.formula_id,
                        "drift": localization.drift.model_dump(mode="json"),
                        "calibrated_confidence": localization.calibrated_confidence,
                    },
                )
            return {
                "proposal_hash": proposal_hash,
                "mutation": governed_mutation.model_dump(mode="json"),
                "localization": localization.model_dump(mode="json"),
                "idempotent": False,
            }

    def simulate(
        self,
        proposal_hash: str,
        *,
        correlation_id: str,
    ) -> dict[str, Any]:
        with self._lock:
            mutation_payload = self._mutations.get(proposal_hash)
            if mutation_payload is None:
                raise MasterSuiteError("proposal_not_found", "Unknown proposal.")
            if proposal_hash in self._simulations:
                return self._simulations[proposal_hash]
            mutation = StrategyMutation.model_validate(mutation_payload)
            localization = mutation.drift_localization
            scenarios = {
                "historical_replay": bool(mutation.evidence_hashes),
                "regime_shift": bool(
                    localization
                    and localization.drift.maximum < 0.80
                ),
                "confidence_collapse": bool(
                    localization
                    and localization.calibrated_confidence >= 0.20
                ),
                "risk_gate_fail_closed": True,
            }
            protected = sorted(
                set(mutation.parameter_diff) & self.PROTECTED_PARAMETERS
            )
            uncertainty = (
                1.0 - localization.calibrated_confidence
                if localization
                else 1.0
            )
            summary = {
                "truth_status": "MODELED",
                "scenario_results": scenarios,
                "protected_parameter_attempts": protected,
                "uncertainty": uncertainty,
                "passed": all(scenarios.values()) and not protected,
                "does_not_imply_approval": True,
            }
            self._record(
                event_type="SimulationCompleted",
                aggregate_type="calibration_proposal",
                aggregate_id=proposal_hash,
                payload=summary,
                correlation_id=correlation_id,
            )
            return summary

    def decide(
        self,
        proposal_hash: str,
        *,
        correlation_id: str,
    ) -> GovernanceDecision:
        with self._lock:
            mutation_payload = self._mutations.get(proposal_hash)
            if mutation_payload is None:
                raise MasterSuiteError("proposal_not_found", "Unknown proposal.")
            existing = self._decisions.get(proposal_hash)
            if existing is not None:
                return GovernanceDecision.model_validate(existing)

            mutation = StrategyMutation.model_validate(mutation_payload)
            simulation = self._simulations.get(proposal_hash)
            reasons: list[str] = []
            if not mutation.evidence_hashes:
                reasons.append("missing_evidence")
            if simulation is None:
                reasons.append("simulation_required")
            elif not simulation.get("passed", False):
                reasons.append("simulation_failed")
            if not mutation.reversibility_plan:
                reasons.append("reversibility_plan_required")
            if set(mutation.parameter_diff) & self.PROTECTED_PARAMETERS:
                reasons.append("protected_parameter_change_attempt")
            if mutation.requested_mode not in self.AUTO_MODES:
                reasons.append("independent_live_approval_unavailable")
            if (
                mutation.drift_localization is None
                or mutation.drift_localization.abstain
            ):
                reasons.append("calibration_abstention")
            if (
                simulation is not None
                and float(simulation.get("uncertainty", 1.0)) > 0.65
            ):
                reasons.append("uncertainty_above_threshold")
            if not self._signing_key:
                reasons.append("signing_key_unavailable")

            if (
                mutation.drift_localization
                and mutation.drift_localization.drift.maximum >= 0.80
            ):
                disposition = GovernanceDisposition.QUARANTINE
            elif reasons:
                disposition = GovernanceDisposition.REVIEW
            else:
                disposition = GovernanceDisposition.ACCEPT

            expires_at = utc_now() + timedelta(minutes=15)
            unsigned = GovernanceDecision(
                proposal_hash=proposal_hash,
                decision=disposition,
                policy_version=self.POLICY_VERSION,
                reason_codes=reasons or ["policy_pass"],
                approved_mode=(
                    mutation.requested_mode
                    if disposition == GovernanceDisposition.ACCEPT
                    else None
                ),
                signer=self._signer,
                signature="",
                expires_at=expires_at,
            )
            signature = (
                self._sign(unsigned.unsigned_payload())
                if self._signing_key
                else ""
            )
            decision = unsigned.model_copy(update={"signature": signature})
            self._record(
                event_type="GovernanceBridgeDecision",
                aggregate_type="calibration_proposal",
                aggregate_id=proposal_hash,
                payload=decision.model_dump(mode="json"),
                correlation_id=correlation_id,
            )
            return decision

    def issue_manifest(
        self,
        proposal_hash: str,
        *,
        expires_in_seconds: int,
        correlation_id: str,
    ) -> RuntimeManifest:
        with self._lock:
            mutation_payload = self._mutations.get(proposal_hash)
            decision_payload = self._decisions.get(proposal_hash)
            if mutation_payload is None or decision_payload is None:
                raise MasterSuiteError(
                    "governance_decision_required",
                    "Proposal and governance decision are required.",
                )
            mutation = StrategyMutation.model_validate(mutation_payload)
            decision = GovernanceDecision.model_validate(decision_payload)
            if decision.decision != GovernanceDisposition.ACCEPT:
                raise MasterSuiteError(
                    "proposal_not_accepted",
                    "Only accepted paper or shadow proposals can receive a manifest.",
                )
            if decision.expires_at <= utc_now():
                raise MasterSuiteError(
                    "governance_decision_expired",
                    "Governance decision has expired.",
                )
            if not self._verify_signature(
                decision.unsigned_payload(),
                decision.signature,
            ):
                raise MasterSuiteError(
                    "invalid_decision_signature",
                    "Governance decision signature is invalid.",
                )
            if decision.approved_mode not in self.AUTO_MODES:
                raise MasterSuiteError(
                    "live_execution_authority_unavailable",
                    "Canary and live manifests are structurally unavailable.",
                )
            if mutation.parent_runtime_hash != self._active_runtime_hash:
                self._raise_alert(
                    proposal_hash=proposal_hash,
                    family="parent_hash_mismatch",
                    severity="unsafe",
                    recommended_next_step="simulate_and_review",
                    correlation_id=correlation_id,
                    context={
                        "expected_parent": self._active_runtime_hash,
                        "proposed_parent": mutation.parent_runtime_hash,
                    },
                )
                raise MasterSuiteError(
                    "parent_runtime_hash_mismatch",
                    "Proposal parent does not match the active runtime.",
                )

            issued_at = utc_now()
            expires_at = issued_at + timedelta(
                seconds=max(60, min(expires_in_seconds, 3600))
            )
            unsigned_payload = {
                "proposal_hash": proposal_hash,
                "decision_id": str(decision.decision_id),
                "parent_runtime_hash": mutation.parent_runtime_hash,
                "target_runtime_hash": canonical_hash(
                    {
                        "formula_id": mutation.formula_id,
                        "candidate_version": mutation.candidate_version,
                        "parameters": mutation.parameter_diff,
                        "proposal_hash": proposal_hash,
                    }
                ),
                "approved_mode": decision.approved_mode.value,
                "issued_at": issued_at.isoformat(),
                "expires_at": expires_at.isoformat(),
                "signer": self._signer,
                "truth_status": "MODELED",
            }
            unsigned_manifest = RuntimeManifest(
                manifest_hash="",
                signature="",
                **unsigned_payload,
            )
            normalized_payload = unsigned_manifest.unsigned_payload()
            signature = self._sign(normalized_payload)
            manifest_hash = canonical_hash(
                {**normalized_payload, "signature": signature}
            )
            manifest = unsigned_manifest.model_copy(
                update={
                    "manifest_hash": manifest_hash,
                    "signature": signature,
                }
            )
            self._record(
                event_type="RuntimeManifestIssued",
                aggregate_type="runtime_manifest",
                aggregate_id=manifest_hash,
                payload=manifest.model_dump(mode="json"),
                correlation_id=correlation_id,
            )
            return manifest

    def acknowledge(
        self,
        manifest_hash: str,
        runtime_instance: str,
        *,
        correlation_id: str,
    ) -> RuntimeAck:
        with self._lock:
            existing_key = f"{manifest_hash}:{runtime_instance}"
            if existing_key in self._acks:
                return RuntimeAck.model_validate(self._acks[existing_key])
            manifest_payload = self._manifests.get(manifest_hash)
            if manifest_payload is None:
                raise MasterSuiteError(
                    "manifest_not_found",
                    "Unknown runtime manifest.",
                )
            manifest = RuntimeManifest.model_validate(manifest_payload)
            reasons = self._manifest_rejection_reasons(manifest)
            if reasons:
                ack = RuntimeAck(
                    manifest_hash=manifest_hash,
                    runtime_instance=runtime_instance,
                    previous_runtime_hash=self._active_runtime_hash,
                    active_runtime_hash=self._active_runtime_hash,
                    status="rejected",
                    reason_codes=reasons,
                    deployment_mode=manifest.approved_mode,
                )
            else:
                ack = RuntimeAck(
                    manifest_hash=manifest_hash,
                    runtime_instance=runtime_instance,
                    previous_runtime_hash=self._active_runtime_hash,
                    active_runtime_hash=manifest.target_runtime_hash,
                    status="applied",
                    reason_codes=["signed_parent_matched_modeled_apply"],
                    deployment_mode=manifest.approved_mode,
                )
            self._record(
                event_type="RuntimeAck",
                aggregate_type="runtime_manifest",
                aggregate_id=manifest_hash,
                payload=ack.model_dump(mode="json"),
                correlation_id=correlation_id,
            )
            if reasons:
                self._raise_alert(
                    proposal_hash=manifest.proposal_hash,
                    family="runtime_manifest_rejected",
                    severity="unsafe",
                    recommended_next_step="quarantine_or_rollback",
                    correlation_id=correlation_id,
                    context={"reason_codes": reasons},
                )
            return ack

    def _manifest_rejection_reasons(
        self,
        manifest: RuntimeManifest,
    ) -> list[str]:
        reasons: list[str] = []
        if not self._manifest_integrity_valid(manifest):
            reasons.append("invalid_manifest_signature")
        if manifest.expires_at <= utc_now():
            reasons.append("manifest_expired")
        if manifest.parent_runtime_hash != self._active_runtime_hash:
            reasons.append("parent_runtime_hash_mismatch")
        decision_payload = self._decisions.get(manifest.proposal_hash)
        if decision_payload is None:
            reasons.append("governance_decision_missing")
        else:
            decision = GovernanceDecision.model_validate(decision_payload)
            if decision.decision != GovernanceDisposition.ACCEPT:
                reasons.append("governance_decision_not_accepted")
            if decision.decision_id != manifest.decision_id:
                reasons.append("governance_decision_mismatch")
        if manifest.approved_mode not in self.AUTO_MODES:
            reasons.append("live_execution_authority_unavailable")
        return reasons

    def _manifest_integrity_valid(
        self,
        manifest: RuntimeManifest,
    ) -> bool:
        unsigned_payload = manifest.unsigned_payload()
        signature_valid = self._verify_signature(
            unsigned_payload,
            manifest.signature,
        )
        expected_hash = canonical_hash(
            {**unsigned_payload, "signature": manifest.signature}
        )
        return signature_valid and hmac.compare_digest(
            expected_hash,
            manifest.manifest_hash,
        )

    def evaluate_risk(
        self,
        intent: OrderIntent,
        manifest_hash: str,
        *,
        correlation_id: str,
    ) -> RiskGateDecision:
        with self._lock:
            manifest_payload = self._manifests.get(manifest_hash)
            manifest = (
                RuntimeManifest.model_validate(manifest_payload)
                if manifest_payload
                else None
            )
            manifest_valid = bool(
                manifest and self._manifest_integrity_valid(manifest)
            )
            decision = self._risk_gate.evaluate(
                intent,
                manifest_valid=manifest_valid,
                manifest_expired=bool(
                    manifest is None or manifest.expires_at <= utc_now()
                ),
                parent_hash_matches=bool(
                    manifest
                    and self._active_runtime_hash
                    in {
                        manifest.parent_runtime_hash,
                        manifest.target_runtime_hash,
                    }
                ),
                approved_mode=(
                    manifest.approved_mode if manifest else None
                ),
            )
            payload = {
                **decision.model_dump(mode="json"),
                "intent_id": str(intent.intent_id),
                "manifest_hash": manifest_hash,
            }
            self._record(
                event_type="RiskGateEvaluated",
                aggregate_type="order_intent",
                aggregate_id=str(intent.intent_id),
                payload=payload,
                correlation_id=correlation_id,
            )
            if not decision.allow:
                self._raise_alert(
                    proposal_hash=(
                        manifest.proposal_hash if manifest else "unknown"
                    ),
                    family="risk_gate_rejection",
                    severity="unsafe",
                    recommended_next_step="simulate_and_review",
                    correlation_id=correlation_id,
                    context={"reason_codes": decision.reason_codes},
                )
            return decision

    def record_outcome(
        self,
        outcome: OutcomeObservation,
        *,
        correlation_id: str,
    ) -> dict[str, Any]:
        with self._lock:
            if outcome.proposal_hash not in self._mutations:
                raise MasterSuiteError("proposal_not_found", "Unknown proposal.")
            payload = outcome.model_dump(mode="json")
            self._record(
                event_type="OutcomeObserved",
                aggregate_type="calibration_proposal",
                aggregate_id=outcome.proposal_hash,
                payload=payload,
                correlation_id=correlation_id,
            )
            mutation = self._mutations[outcome.proposal_hash]
            decision = self._decisions.get(outcome.proposal_hash, {})
            matching_ack = next(
                (
                    ack
                    for ack in self._acks.values()
                    if self._manifests.get(
                        ack["manifest_hash"],
                        {},
                    ).get("proposal_hash")
                    == outcome.proposal_hash
                ),
                {},
            )
            exemplar = {
                "episode_id": str(outcome.outcome_id),
                "proposal_hash": outcome.proposal_hash,
                "regime_signature": mutation.get(
                    "drift_localization",
                    {},
                ).get("reference_window_hash"),
                "drift_vector": mutation.get(
                    "drift_localization",
                    {},
                ).get("drift", {}),
                "formula_version": mutation["candidate_version"],
                "parameter_hash": canonical_hash(
                    mutation["parameter_diff"]
                ),
                "decision_id": decision.get("decision_id"),
                "deployment_mode": matching_ack.get("deployment_mode"),
                "rollback_flag": matching_ack.get("status")
                == "rolled_back",
                "outcome_quality": max(
                    0.0,
                    1.0 - outcome.calibration_error - outcome.error_rate,
                ),
                "truth_status": outcome.truth_status,
            }
            self._journal.append_gold_exemplar(exemplar)
            return {"outcome": payload, "gold_exemplar": exemplar}

    def _raise_alert(
        self,
        *,
        proposal_hash: str,
        family: str,
        severity: str,
        recommended_next_step: str,
        correlation_id: str,
        context: dict[str, Any],
    ) -> None:
        payload = {
            "proposal_hash": proposal_hash,
            "family": family,
            "severity": severity,
            "recommended_next_step": recommended_next_step,
            "context": context,
            "raised_at": utc_now().isoformat(),
            "truth_status": "MODELED",
        }
        self._record(
            event_type="RuntimeAlertRaised",
            aggregate_type="runtime_alert",
            aggregate_id=canonical_hash(payload),
            payload=payload,
            correlation_id=correlation_id,
        )

    @staticmethod
    def _localization_severity(maximum_drift: float) -> str:
        if maximum_drift >= 0.80:
            return "unsafe"
        if maximum_drift >= 0.55:
            return "degraded"
        if maximum_drift >= 0.25:
            return "watch"
        return "watch"

    def dashboard_state(self) -> dict[str, Any]:
        with self._lock:
            proposals = len(self._mutations)
            simulations = len(self._simulations)
            manifests = [
                RuntimeManifest.model_validate(item)
                for item in self._manifests.values()
            ]
            valid_manifests = sum(
                self._verify_signature(
                    manifest.unsigned_payload(),
                    manifest.signature,
                )
                and manifest.expires_at > utc_now()
                for manifest in manifests
            )
            invalid_receipts = sum(
                any(
                    code
                    in {
                        "invalid_manifest_signature",
                        "manifest_expired",
                        "invalid_decision_signature",
                    }
                    for code in ack.get("reason_codes", [])
                )
                for ack in self._acks.values()
            )
            parent_mismatches = sum(
                "parent_runtime_hash_mismatch"
                in ack.get("reason_codes", [])
                for ack in self._acks.values()
            )
            risk_total = len(self._risk_decisions)
            risk_rejected = sum(
                not item.get("allow", False)
                for item in self._risk_decisions
            )
            localizations = [
                item["drift_localization"]
                for item in self._mutations.values()
                if item.get("drift_localization")
            ]
            latest = localizations[-1] if localizations else None
            latest_drift = (
                latest.get("drift", {}) if latest is not None else {}
            )
            latest_max = max(
                [
                    float(value)
                    for key, value in latest_drift.items()
                    if key.endswith("_drift") or key == "regime_novelty"
                ],
                default=0.0,
            )
            return {
                "generated_at": utc_now().isoformat(),
                "truth_status": "MODELED",
                "regulatory_claim": "NOT_ASSESSED",
                "execution_authority": "UNAVAILABLE",
                "broker_connector": "UNAVAILABLE",
                "overall_status": (
                    "unsafe"
                    if self._integrity_status != "VERIFIED"
                    else "watch"
                ),
                "active_runtime_hash": self._active_runtime_hash,
                "journal": {
                    **self._journal.integrity_head,
                    "integrity_status": self._integrity_status,
                },
                "sections": {
                    "control_isolation": {
                        "status": "watch",
                        "truth_status": "MODELED",
                        "risk_control_owner": self._risk_gate.controls.control_owner,
                        "strategy_write_path": False,
                    },
                    "drift_localization": {
                        "status": self._localization_severity(latest_max),
                        "truth_status": "MODELED",
                        "latest": latest,
                    },
                    "runtime_apply_ack": {
                        "status": "watch",
                        "truth_status": "MODELED",
                        "paper_shadow_only": True,
                        "ack_count": len(self._acks),
                    },
                    "receipt_validity": {
                        "status": (
                            "unsafe" if invalid_receipts else "watch"
                        ),
                        "truth_status": "MODELED",
                        "signing_key_source": (
                            "environment"
                            if self._signing_key
                            else "UNAVAILABLE"
                        ),
                    },
                    "kill_switch_readiness": {
                        "status": "watch",
                        "truth_status": "MODELED",
                        "engaged": self._risk_gate.controls.kill_switch_engaged,
                        "broker_enforcement": "UNAVAILABLE",
                    },
                },
                "metrics": {
                    "runtime_transitions_with_valid_signed_manifest_pct": (
                        100.0 * valid_manifests / len(manifests)
                        if manifests
                        else 0.0
                    ),
                    "proposals_with_simulation_pct": (
                        100.0 * simulations / proposals
                        if proposals
                        else 0.0
                    ),
                    "invalid_receipt_count": invalid_receipts,
                    "parent_hash_mismatch_count": parent_mismatches,
                    "localized_drift_severity": latest_max,
                    "interval_coverage_deviation": (
                        latest.get("coverage_deviation")
                        if latest
                        else None
                    ),
                    "abstention_rate": (
                        sum(item.get("abstain", True) for item in localizations)
                        / len(localizations)
                        if localizations
                        else 0.0
                    ),
                    "raw_vs_calibrated_confidence_gap": (
                        max(
                            0.0,
                            -float(
                                self._mutations[
                                    next(reversed(self._mutations))
                                ].get("confidence_delta", 0.0)
                            ),
                        )
                        if self._mutations
                        else 0.0
                    ),
                    "risk_gate_rejection_rate": (
                        risk_rejected / risk_total if risk_total else 0.0
                    ),
                },
                "chain_counts": {
                    "mutations": proposals,
                    "simulations": simulations,
                    "decisions": len(self._decisions),
                    "manifests": len(self._manifests),
                    "acks": len(self._acks),
                    "outcomes": len(self._outcomes),
                },
            }

    def alerts(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(reversed(self._alerts[-100:]))

    def exemplars(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    "proposal_hash": proposal_hash,
                    "mutation": mutation,
                    "decision": self._decisions.get(proposal_hash),
                    "simulation": self._simulations.get(proposal_hash),
                    "outcome": self._outcomes.get(proposal_hash),
                }
                for proposal_hash, mutation in self._mutations.items()
            ]
