from __future__ import annotations

import json
import os
import hashlib
from pathlib import Path
from typing import Any

from .persistence import ReceiptLedger, canonical_json


class ExemplarStore:
    """Append-only incident memory backed by the same local hash-chain primitive."""

    def __init__(self, path: Path | None = None) -> None:
        data_dir = Path(os.getenv("GDW_DATA_DIR", "data"))
        self.path = path or data_dir / "incident-exemplars.jsonl"

    def _ledger(self) -> ReceiptLedger:
        return ReceiptLedger(
            self.path,
            signing_state_path=self.path.with_suffix(
                self.path.suffix + ".signing-state.json"
            ),
        )

    def append(self, record: dict[str, Any]) -> str:
        return self._ledger().append({"kind": "incident_exemplar", "record": record})

    def ensure_signed_head(self, runtime_identity: dict[str, Any]) -> str | None:
        """Anchor an empty or legacy memory chain when receipt signing is required."""
        return self._ledger().ensure_signed_head(runtime_identity)

    def append_once(
        self,
        record: dict[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> tuple[str, bool]:
        record_digest = hashlib.sha256(canonical_json(record)).hexdigest()
        return self._ledger().append_once(
            {"kind": "incident_exemplar", "record": record, "record_digest": record_digest},
            idempotency_key=idempotency_key or record_digest,
        )

    def load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        exemplars: list[dict[str, Any]] = []
        for raw_line in self.path.read_text(encoding="utf-8").splitlines():
            envelope = json.loads(raw_line)
            body = envelope.get("body", {})
            if body.get("kind") != "incident_exemplar" or not isinstance(body.get("record"), dict):
                continue
            exemplars.append(
                {
                    **body["record"],
                    "record_hash": envelope.get("receipt_hash"),
                    "record_digest": body.get("record_digest"),
                }
            )
        return exemplars

    def verify(self) -> tuple[bool, int]:
        return self._ledger().verify()


def load_exemplars(path: Path | None = None) -> list[dict[str, Any]]:
    return ExemplarStore(path).load()
