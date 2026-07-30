from __future__ import annotations

import hashlib
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from threading import Lock
from typing import Any, BinaryIO

WINDOWS_LOCK_TIMEOUT_SECONDS = 30.0
WINDOWS_LOCK_RETRY_SECONDS = 0.01


def canonical_json(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


class ReceiptLedger:
    """Append-only local hash chain. It is deliberately not described as signed."""

    def __init__(self, path: Path | None = None) -> None:
        data_dir = Path(os.getenv("GDW_DATA_DIR", "data"))
        self.path = path or data_dir / "receipts.jsonl"
        self._lock = Lock()

    def append(self, body: dict[str, Any]) -> str:
        with self._lock:
            with self._process_lock():
                previous_hash = self._last_hash()
                envelope = {"previous_hash": previous_hash, "body": body}
                receipt_hash = hashlib.sha256(canonical_json(envelope)).hexdigest()
                record = {**envelope, "receipt_hash": receipt_hash}
                with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                    handle.write(canonical_json(record).decode("utf-8") + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                return receipt_hash

    def verify(self) -> tuple[bool, int]:
        valid, count, _ = self.status()
        return valid, count

    def status(self) -> tuple[bool, int, str | None]:
        with self._lock:
            with self._process_lock():
                previous_hash: str | None = None
                count = 0
                if not self.path.exists():
                    return True, 0, None
                for raw_line in self.path.read_text(encoding="utf-8").splitlines():
                    record = json.loads(raw_line)
                    if record.get("previous_hash") != previous_hash:
                        return False, count, previous_hash
                    envelope = {"previous_hash": previous_hash, "body": record.get("body")}
                    expected = hashlib.sha256(canonical_json(envelope)).hexdigest()
                    if record.get("receipt_hash") != expected:
                        return False, count, previous_hash
                    previous_hash = expected
                    count += 1
                return True, count, previous_hash

    def _last_hash(self) -> str | None:
        if not self.path.exists():
            return None
        lines = self.path.read_text(encoding="utf-8").splitlines()
        if not lines:
            return None
        return str(json.loads(lines[-1])["receipt_hash"])

    @contextmanager
    def _process_lock(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        with lock_path.open("a+b", buffering=0) as handle:
            if os.name == "nt":
                import msvcrt

                self._acquire_windows_lock(handle, msvcrt)
                try:
                    yield
                finally:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _acquire_windows_lock(handle: BinaryIO, msvcrt_module: Any) -> None:
        deadline = time.monotonic() + WINDOWS_LOCK_TIMEOUT_SECONDS
        last_error: OSError | None = None

        while os.fstat(handle.fileno()).st_size < 1:
            try:
                handle.seek(0)
                handle.write(b"\0")
            except OSError as error:
                last_error = error
                if time.monotonic() >= deadline:
                    raise TimeoutError("timed out initializing the receipt ledger lock file") from last_error
                time.sleep(WINDOWS_LOCK_RETRY_SECONDS)

        while True:
            try:
                handle.seek(0)
                msvcrt_module.locking(handle.fileno(), msvcrt_module.LK_NBLCK, 1)
                return
            except OSError as error:
                last_error = error
                if time.monotonic() >= deadline:
                    raise TimeoutError("timed out acquiring the receipt ledger lock") from last_error
                time.sleep(WINDOWS_LOCK_RETRY_SECONDS)
