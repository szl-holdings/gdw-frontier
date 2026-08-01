from __future__ import annotations

import hashlib
import os
import threading
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.persistence import ReceiptLedger
from gdw_x.control_plane.reconciliation import reconcile
from gdw_x.data_plane.risk_gateway import RiskGateway
from gdw_x.domain.contracts import (
    ApprovalManifest,
    CalibrationEpisode,
    CalibrationProposal,
    Decision,
    DeploymentMode,
    GovernanceDecision,
    ProposalState,
    RuntimeAck,
    RuntimeStatus,
    SimulationResult,
    canonical_digest,
)
from gdw_x.domain.simulator import default_scenarios, run_scenario
from gdw_x.governance.kernel import GovernanceKernel
from gdw_x.governance.receipts import ManifestAuthenticator, deterministic_receipt_hash
from gdw_x.memory.exemplar_store import LakeExemplarStore

ZERO_HASH = "0" * 64


class CalibrationService:
    def __init__(
        self,
        data_dir: Path,
        *,
        signing_key: bytes | None = None,
        signing_key_source: str | None = None,
    ) -> None:
        environment_key = os.getenv("GDWX_MANIFEST_SIGNING_KEY")
        key = signing_key or (environment_key.encode("utf-8") if environment_key else None)
        source = signing_key_source or ("environment" if environment_key else None)
        self.authenticator = ManifestAuthenticator(key, source=source)
        self.gateway = RiskGateway(self.authenticator)
        self.kernel = GovernanceKernel()
        self.ledger = ReceiptLedger(data_dir / "calibration-receipts.jsonl")
        self.lake = LakeExemplarStore(data_dir / "lake")
        self.scenarios = default_scenarios()
        self.runtime = RuntimeStatus(
            runtime_instance="gdwx-local",
            active_runtime_hash=ZERO_HASH,
            active_mode=DeploymentMode.OFFLINE,
        )
        self.proposals: dict[str, CalibrationProposal] = {}
        self.states: dict[str, ProposalState] = {}
        self.simulations: dict[str, list[SimulationResult]] = {}
        self.decisions: dict[str, GovernanceDecision] = {}
        self.manifests: dict[str, ApprovalManifest] = {}
        self.acks: dict[str, RuntimeAck] = {}
        self._lock = threading.RLock()

    def _receipt(self, event: str, subject_hash: str, state: ProposalState) -> str:
        content_hash = deterministic_receipt_hash(
            event=event,
            subject_hash=subject_hash,
            state=state.value,
            policy_version=self.kernel.policy.version,
        )
        self.ledger.append_once(
            {
                "schema_version": "gdwx.content-receipt.v1",
                "event": event,
                "subject_hash": subject_hash,
                "state": state.value,
                "policy_version": self.kernel.policy.version,
                "content_receipt_hash": content_hash,
                "model_status": "MODELED",
            },
            idempotency_key=content_hash,
        )
        return content_hash

    def create_proposal(self, proposal: CalibrationProposal) -> dict[str, Any]:
        proposal_hash = proposal.canonical_hash()
        with self._lock:
            created = proposal_hash not in self.proposals
            if created:
                self.proposals[proposal_hash] = proposal
                self.states[proposal_hash] = ProposalState.DRAFT
            receipt_hash = self._receipt("CalibrationProposed", proposal_hash, self.states[proposal_hash])
            return {
                "proposal_hash": proposal_hash,
                "state": self.states[proposal_hash].value,
                "created": created,
                "receipt_hash": receipt_hash,
                "status": "MODELED",
            }

    def simulate(self, proposal_hash: str, scenario_ids: list[str]) -> dict[str, Any]:
        if not scenario_ids:
            raise ValueError("scenario_ids required")
        with self._lock:
            proposal = self.proposals.get(proposal_hash)
            if proposal is None:
                raise KeyError("unknown proposal")
            unknown = sorted(set(scenario_ids) - set(self.scenarios))
            if unknown:
                raise ValueError(f"unknown scenarios: {', '.join(unknown)}")
            multiplier = max(0.0, 1.0 - proposal.uncertainty)
            results = [
                run_scenario(self.scenarios[scenario_id], confidence_multiplier=multiplier)
                for scenario_id in scenario_ids
            ]
            self.simulations[proposal_hash] = results
            self.states[proposal_hash] = ProposalState.SIMULATED
            receipt_hash = self._receipt("SimulationCompleted", proposal_hash, ProposalState.SIMULATED)
            return {
                "proposal_hash": proposal_hash,
                "state": ProposalState.SIMULATED.value,
                "results": [result.model_dump(mode="json") for result in results],
                "promotion_eligible": False,
                "receipt_hash": receipt_hash,
                "note": "simulation does not imply approval",
                "status": "MODELED",
            }

    def decide(self, proposal_hash: str) -> GovernanceDecision:
        with self._lock:
            proposal = self.proposals.get(proposal_hash)
            if proposal is None:
                raise KeyError("unknown proposal")
            decision, reasons, approved_mode = self.kernel.evaluate(
                proposal,
                self.simulations.get(proposal_hash, []),
            )
            if decision == Decision.ACCEPT and approved_mode == DeploymentMode.PAPER:
                state = ProposalState.APPROVED_FOR_PAPER
            elif decision == Decision.ACCEPT and approved_mode == DeploymentMode.SHADOW:
                state = ProposalState.APPROVED_FOR_SHADOW
            elif decision == Decision.QUARANTINE:
                state = ProposalState.QUARANTINED
            elif decision == Decision.REJECT:
                state = ProposalState.REJECTED
            else:
                state = ProposalState.SIMULATED
            self.states[proposal_hash] = state
            unsigned = {
                "proposal_hash": proposal_hash,
                "decision": decision.value,
                "policy_version": self.kernel.policy.version,
                "reason_codes": reasons,
                "approved_mode": approved_mode.value if approved_mode else None,
                "signer_ref": self.authenticator.signer_ref,
                "signature_algorithm": "HMAC-SHA256",
                "signing_key_source": self.authenticator.source,
            }
            governance_decision = GovernanceDecision.model_validate(
                {**unsigned, "signature": self.authenticator.sign(unsigned)}
            )
            self.decisions[proposal_hash] = governance_decision
            self._receipt("GovernanceDecision", proposal_hash, state)
            return governance_decision

    def issue_manifest(self, proposal_hash: str, ttl_seconds: int = 900) -> ApprovalManifest:
        if ttl_seconds < 1 or ttl_seconds > 3_600:
            raise ValueError("manifest ttl must be between 1 and 3600 seconds")
        with self._lock:
            proposal = self.proposals.get(proposal_hash)
            decision = self.decisions.get(proposal_hash)
            if proposal is None or decision is None:
                raise KeyError("proposal or governance decision unavailable")
            if decision.decision != Decision.ACCEPT or decision.approved_mode not in {
                DeploymentMode.PAPER,
                DeploymentMode.SHADOW,
            }:
                raise PermissionError("proposal is not approved for paper or shadow")
            target_hash = canonical_digest(
                {
                    "proposal_hash": proposal_hash,
                    "candidate_version": proposal.candidate_version,
                    "parameters": proposal.proposed_parameters,
                    "confidence_transform": proposal.proposed_confidence_transform,
                    "parent_runtime_hash": proposal.parent_runtime_hash,
                }
            )
            issued_at = datetime.now(timezone.utc)
            manifest = self.authenticator.issue_manifest(
                proposal_hash=proposal_hash,
                parent_runtime_hash=proposal.parent_runtime_hash,
                target_runtime_hash=target_hash,
                approved_mode=decision.approved_mode,
                policy_version=decision.policy_version,
                issued_at=issued_at,
                expires_at=issued_at + timedelta(seconds=ttl_seconds),
            )
            manifest_hash = manifest.canonical_hash()
            self.manifests[manifest_hash] = manifest
            self._receipt("DeploymentManifestIssued", manifest_hash, self.states[proposal_hash])
            return manifest

    def apply_manifest(self, manifest_hash: str) -> RuntimeAck:
        with self._lock:
            if manifest_hash in self.acks:
                cached_ack = self.acks[manifest_hash]
                if (
                    cached_ack.status == "applied"
                    and self.runtime.applied_manifest_hash != manifest_hash
                ):
                    raise PermissionError(
                        "manifest acknowledgement is superseded by the current runtime binding"
                    )
                return cached_ack
            manifest = self.manifests.get(manifest_hash)
            if manifest is None:
                raise KeyError("unknown manifest")
            result = reconcile(
                manifest,
                self.runtime,
                gateway=self.gateway,
                now=datetime.now(timezone.utc),
            )
            previous_hash = self.runtime.active_runtime_hash
            if result["action"] == "apply":
                state = (
                    ProposalState.PAPER_ACTIVE
                    if manifest.approved_mode == DeploymentMode.PAPER
                    else ProposalState.SHADOW_ACTIVE
                )
                self.states[manifest.proposal_hash] = state
                self.runtime = RuntimeStatus(
                    runtime_instance=self.runtime.runtime_instance,
                    active_runtime_hash=manifest.target_runtime_hash,
                    active_mode=manifest.approved_mode,
                    active_proposal_hash=manifest.proposal_hash,
                    applied_manifest_hash=manifest_hash,
                )
                status = "applied"
            elif result["action"] == "noop":
                state = self.states[manifest.proposal_hash]
                self.runtime = self.runtime.model_copy(
                    update={"applied_manifest_hash": manifest_hash}
                )
                status = "applied"
            else:
                state = ProposalState.QUARANTINED
                self.states[manifest.proposal_hash] = state
                status = "rejected"
            ack = RuntimeAck(
                proposal_hash=manifest.proposal_hash,
                runtime_instance=self.runtime.runtime_instance,
                previous_runtime_hash=previous_hash,
                active_runtime_hash=self.runtime.active_runtime_hash,
                active_mode=self.runtime.active_mode,
                status=status,
                reason=result["reason"],
                applied_at=datetime.now(timezone.utc),
                manifest_hash=manifest_hash,
            )
            self.acks[manifest_hash] = ack
            self._receipt("RuntimeAck", ack.canonical_hash(), state)
            return ack

    def record_outcome(
        self,
        *,
        proposal_hash: str,
        manifest_hash: str,
        runtime_instance: str,
        observed_runtime_hash: str,
        regime_id: str,
        parameter_hash: str,
        observed_utility: float,
        rollback_status: str,
        parent_episode_hash: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            proposal = self.proposals.get(proposal_hash)
            decision = self.decisions.get(proposal_hash)
            if proposal is None or decision is None:
                raise KeyError("unknown proposal or decision")
            matching_ack = self.acks.get(manifest_hash)
            manifest = self.manifests.get(manifest_hash)
            if (
                matching_ack is None
                or manifest is None
                or matching_ack.status != "applied"
                or manifest.proposal_hash != proposal_hash
                or matching_ack.proposal_hash != proposal_hash
            ):
                raise PermissionError(
                    "an applied runtime acknowledgement is required before recording an outcome"
                )
            if (
                matching_ack.manifest_hash != manifest_hash
                or matching_ack.runtime_instance != runtime_instance
                or matching_ack.active_runtime_hash != observed_runtime_hash
                or manifest.target_runtime_hash != observed_runtime_hash
                or self.runtime.runtime_instance != runtime_instance
                or self.runtime.active_runtime_hash != observed_runtime_hash
                or self.runtime.active_proposal_hash != proposal_hash
                or self.runtime.applied_manifest_hash != manifest_hash
            ):
                raise PermissionError(
                    "outcome binding does not match the current applied runtime"
                )
            episode = CalibrationEpisode(
                episode_id=hashlib.sha256(
                    (
                        f"{proposal_hash}:{manifest_hash}:{runtime_instance}:"
                        f"{observed_runtime_hash}:{regime_id}:{parameter_hash}"
                    ).encode("utf-8")
                ).hexdigest(),
                recorded_at=datetime.now(timezone.utc),
                formula_id=proposal.formula_id,
                formula_version=proposal.candidate_version,
                regime_id=regime_id,
                parameter_hash=parameter_hash,
                drift=proposal.drift,
                proposal_hash=proposal_hash,
                manifest_hash=manifest_hash,
                runtime_instance=runtime_instance,
                observed_runtime_hash=observed_runtime_hash,
                decision=decision.decision,
                deployment_mode=matching_ack.active_mode,
                observed_utility=observed_utility,
                predicted_utility=proposal.expected_utility_delta,
                rollback_status=rollback_status,
                evidence_hashes=proposal.evidence_hashes,
                uncertainty=proposal.uncertainty,
                parent_episode_hash=parent_episode_hash,
            )
            record_hash, created = self.lake.store(episode)
            return {
                "episode_hash": episode.canonical_hash(),
                "record_hash": record_hash,
                "created": created,
                "status": "MODELED",
            }

    def search_exemplars(
        self,
        *,
        formula_id: str,
        regime_id: str,
        proposal_hash: str,
        k: int,
    ) -> list[dict[str, Any]]:
        proposal = self.proposals.get(proposal_hash)
        if proposal is None:
            raise KeyError("unknown proposal")
        return self.lake.retrieve(
            formula_id=formula_id,
            regime_id=regime_id,
            drift=proposal.drift,
            uncertainty=proposal.uncertainty,
            now=datetime.now(timezone.utc),
            k=k,
        )

    def status(self) -> dict[str, Any]:
        with self._lock:
            counts = Counter(state.value for state in self.states.values())
            alerts = []
            if self.authenticator.source == "ephemeral_process":
                alerts.append("EPHEMERAL_SIGNING_KEY")
            if self.runtime.active_mode in {DeploymentMode.CANARY, DeploymentMode.LIVE}:
                alerts.append("UNSUPPORTED_MARKET_AUTHORITY")
            return {
                "service": "szl-calibration-intelligence-plane",
                "status": "MODELED",
                "ship_modes": ["offline", "paper", "shadow"],
                "unavailable_modes": ["canary", "live"],
                "policy_version": self.kernel.policy.version,
                "signing_key_source": self.authenticator.source,
                "runtime": self.runtime.model_dump(mode="json"),
                "proposal_state_counts": dict(sorted(counts.items())),
                "scenario_count": len(self.scenarios),
                "alerts": alerts,
                "truth_boundary": (
                    "No broker connectivity, order authority, production signature, or trading-edge claim."
                ),
            }
