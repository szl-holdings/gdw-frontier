from __future__ import annotations

import json
import multiprocessing
from pathlib import Path

from app.persistence import ReceiptLedger


def _append_records(path: str, worker: int, count: int) -> None:
    ledger = ReceiptLedger(Path(path))
    for index in range(count):
        ledger.append({"worker": worker, "index": index})


def test_receipt_chain_verifies_and_detects_tampering(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    ledger = ReceiptLedger(path)
    first_hash = ledger.append({"decision": "allow"})
    second_hash = ledger.append({"decision": "review"})

    assert first_hash != second_hash
    assert ledger.verify() == (True, 2)

    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    records[0]["body"]["decision"] = "withhold"
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
    assert ledger.verify()[0] is False


def test_receipt_chain_is_safe_across_worker_processes(tmp_path: Path) -> None:
    path = tmp_path / "multiprocess-ledger.jsonl"
    context = multiprocessing.get_context("spawn")
    processes = [
        context.Process(target=_append_records, args=(str(path), worker, 5))
        for worker in range(4)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0
    assert ReceiptLedger(path).verify() == (True, 20)
