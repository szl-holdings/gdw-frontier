from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from gdw_x.domain.master_suite import canonical_hash


def resolve_master_suite_data_root(data_root: Path | None = None) -> Path:
    if data_root is not None:
        return data_root
    configured = os.getenv("GDWX_MASTER_SUITE_DATA_ROOT", "").strip()
    if configured:
        return Path(configured)
    project_root = Path(__file__).resolve().parents[2]
    shared_data_root = Path(
        os.getenv("GDW_DATA_DIR", str(project_root / "data"))
    )
    return shared_data_root / "master_suite"


class JournalIntegrityError(RuntimeError):
    pass


class LakeJournal:
    """Append-only bronze journal with derived silver and gold projections."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.bronze = root / "bronze"
        self.silver = root / "silver"
        self.gold = root / "gold"
        self.events_path = self.bronze / "events.jsonl"
        self.state_path = self.silver / "master_suite_state.json"
        self.exemplars_path = self.gold / "exemplars.jsonl"
        self._lock = RLock()
        self._last_hash = "GENESIS"
        self._last_timestamp: datetime | None = None
        self._sequence = 0
        self._bootstrap()

    def _bootstrap(self) -> None:
        for event in self.read_events():
            self._sequence = event["sequence"]
            self._last_hash = event["event_hash"]
            self._last_timestamp = datetime.fromisoformat(event["occurred_at"])

    def read_events(self) -> list[dict[str, Any]]:
        if not self.events_path.exists():
            return []
        events: list[dict[str, Any]] = []
        expected_parent = "GENESIS"
        expected_sequence = 1
        with self.events_path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                event = json.loads(line)
                claimed_hash = event.pop("event_hash", None)
                actual_hash = canonical_hash(event)
                event["event_hash"] = claimed_hash
                if claimed_hash != actual_hash:
                    raise JournalIntegrityError(
                        f"bronze event hash mismatch at line {line_number}"
                    )
                if event.get("previous_event_hash") != expected_parent:
                    raise JournalIntegrityError(
                        f"bronze parent hash mismatch at line {line_number}"
                    )
                if event.get("sequence") != expected_sequence:
                    raise JournalIntegrityError(
                        f"bronze sequence mismatch at line {line_number}"
                    )
                expected_parent = claimed_hash
                expected_sequence += 1
                events.append(event)
        return events

    def append(
        self,
        *,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        payload: dict[str, Any],
        correlation_id: str,
    ) -> dict[str, Any]:
        with self._lock:
            now = datetime.now(timezone.utc)
            if self._last_timestamp is not None and now <= self._last_timestamp:
                now = self._last_timestamp + timedelta(microseconds=1)
            event = {
                "event_id": str(uuid4()),
                "sequence": self._sequence + 1,
                "occurred_at": now.isoformat(),
                "event_type": event_type,
                "aggregate_type": aggregate_type,
                "aggregate_id": aggregate_id,
                "correlation_id": correlation_id,
                "previous_event_hash": self._last_hash,
                "payload": payload,
            }
            event["event_hash"] = canonical_hash(event)
            self.bronze.mkdir(parents=True, exist_ok=True)
            with self.events_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(event, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            self._sequence = event["sequence"]
            self._last_hash = event["event_hash"]
            self._last_timestamp = now
            return event

    def write_silver_snapshot(self, state: dict[str, Any]) -> None:
        with self._lock:
            self.silver.mkdir(parents=True, exist_ok=True)
            temporary = self.state_path.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(state, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            os.replace(temporary, self.state_path)

    def append_gold_exemplar(self, exemplar: dict[str, Any]) -> None:
        with self._lock:
            self.gold.mkdir(parents=True, exist_ok=True)
            with self.exemplars_path.open(
                "a",
                encoding="utf-8",
                newline="\n",
            ) as handle:
                handle.write(json.dumps(exemplar, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())

    @property
    def integrity_head(self) -> dict[str, str | int]:
        return {
            "sequence": self._sequence,
            "event_hash": self._last_hash,
            "status": "VERIFIED" if self._sequence else "EMPTY",
        }
