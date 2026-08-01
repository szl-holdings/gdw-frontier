from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import BinaryIO

from gdw_x.memory.lake_journal import resolve_master_suite_data_root


def governed_worker_count() -> int:
    signals: list[tuple[str, str]] = []
    for name in ("GDW_WORKERS", "WEB_CONCURRENCY", "UVICORN_WORKERS"):
        configured = os.getenv(name, "").strip()
        if configured:
            signals.append((name, configured))

    if os.getenv("UVICORN_LIFESPAN", "").strip().casefold() == "off":
        raise RuntimeError(
            "UVICORN_LIFESPAN=off is unsupported because governed startup controls require lifespan"
        )

    arguments = sys.argv[1:]
    for index, argument in enumerate(arguments):
        if argument == "--lifespan":
            value = arguments[index + 1] if index + 1 < len(arguments) else ""
            if value.casefold() == "off":
                raise RuntimeError(
                    "uvicorn --lifespan off is unsupported because governed startup controls require lifespan"
                )
        elif argument.startswith("--lifespan="):
            if argument.split("=", 1)[1].casefold() == "off":
                raise RuntimeError(
                    "uvicorn --lifespan off is unsupported because governed startup controls require lifespan"
                )
        elif argument == "--workers":
            value = arguments[index + 1] if index + 1 < len(arguments) else ""
            signals.append(("uvicorn --workers", value))
        elif argument.startswith("--workers="):
            signals.append(("uvicorn --workers", argument.split("=", 1)[1]))

    for source, configured in signals or [("GDW_WORKERS", "1")]:
        try:
            workers = int(configured)
        except ValueError as error:
            raise RuntimeError(f"{source} must be the integer 1") from error
        if workers != 1:
            raise RuntimeError(
                f"{source} must be 1 while Master Suite state is process-local"
            )
    return 1


class MasterSuiteProcessLease:
    """Exclusive OS lease preventing multiple ASGI workers from sharing stale state."""

    def __init__(self, data_root: Path | None = None) -> None:
        self.path = resolve_master_suite_data_root(data_root) / ".process.lock"
        self._handle: BinaryIO | None = None

    def acquire(self) -> None:
        if self._handle is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            handle.close()
            raise RuntimeError(
                "another Master Suite process already owns the shared data root"
            ) from error
        self._handle = handle

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
            self._handle = None
