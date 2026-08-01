from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.persistence import ReceiptLedger
from gdw_x.domain.contracts import CalibrationEpisode, DriftVector


class LakeExemplarStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.bronze = root / "bronze" / "episodes.jsonl"
        self.silver = root / "silver" / "episodes.jsonl"
        self.gold = root / "gold" / "episodes.jsonl"

    def store(self, episode: CalibrationEpisode) -> tuple[str, bool]:
        episode_hash = episode.canonical_hash()
        record = {"kind": "calibration_episode", "episode": episode.model_dump(mode="json")}
        ReceiptLedger(self.bronze).append_once({**record, "tier": "bronze"}, episode_hash)
        ReceiptLedger(self.silver).append_once({**record, "tier": "silver"}, episode_hash)
        return ReceiptLedger(self.gold).append_once({**record, "tier": "gold"}, episode_hash)

    def load_gold(self) -> list[dict[str, Any]]:
        if not self.gold.exists():
            return []
        rows: list[dict[str, Any]] = []
        for raw_line in self.gold.read_text(encoding="utf-8").splitlines():
            envelope = json.loads(raw_line)
            body = envelope.get("body", {})
            if body.get("kind") == "calibration_episode":
                rows.append({**body["episode"], "record_hash": envelope["receipt_hash"]})
        return rows

    def retrieve(
        self,
        *,
        formula_id: str,
        regime_id: str,
        drift: DriftVector,
        uncertainty: float,
        now: datetime,
        k: int = 5,
    ) -> list[dict[str, Any]]:
        scored: list[tuple[float, dict[str, Any]]] = []
        query_drift = drift.model_dump()
        for row in self.load_gold():
            candidate = CalibrationEpisode.model_validate(
                {key: value for key, value in row.items() if key != "record_hash"}
            )
            runtime_binding_status = (
                "BOUND"
                if all(
                    (
                        candidate.manifest_hash,
                        candidate.runtime_instance,
                        candidate.observed_runtime_hash,
                    )
                )
                else "LEGACY_UNBOUND"
            )
            regime_score = 1.0 if candidate.regime_id == regime_id else 0.0
            formula_score = 1.0 if candidate.formula_id == formula_id else 0.0
            candidate_drift = candidate.drift.model_dump()
            drift_distance = sum(abs(query_drift[key] - candidate_drift[key]) for key in query_drift)
            drift_score = math.exp(-drift_distance)
            candidate_time = candidate.recorded_at
            if candidate_time.tzinfo is None:
                candidate_time = candidate_time.replace(tzinfo=timezone.utc)
            age_days = max(0.0, (now - candidate_time).total_seconds() / 86_400.0)
            recency_score = math.exp(-age_days / 180.0)
            score = (
                0.35 * regime_score
                + 0.30 * drift_score
                + 0.20 * formula_score
                + 0.15 * recency_score
                - 0.20 * max(uncertainty, candidate.uncertainty)
            )
            scored.append(
                (
                    score,
                    {
                        **candidate.model_dump(mode="json"),
                        "record_hash": row["record_hash"],
                        "runtime_binding_status": runtime_binding_status,
                        "retrieval_score": round(score, 6),
                        "score_components": {
                            "regime": regime_score,
                            "drift": round(drift_score, 6),
                            "formula": formula_score,
                            "recency": round(recency_score, 6),
                        },
                    },
                )
            )
        scored.sort(key=lambda item: (item[0], item[1]["record_hash"]), reverse=True)
        return [row for _, row in scored[:k]]
