from __future__ import annotations

import os
import sys
from pathlib import Path

import uvicorn


def prepare_multiprocess_metrics() -> None:
    configured = os.getenv("PROMETHEUS_MULTIPROC_DIR")
    if not configured:
        return
    directory = Path(configured)
    directory.mkdir(parents=True, exist_ok=True)
    for shard in directory.iterdir():
        if shard.is_file() and shard.suffix == ".db":
            shard.unlink()


def governed_worker_count() -> int:
    signals: list[tuple[str, str]] = []
    for name in ("GDW_WORKERS", "WEB_CONCURRENCY"):
        configured = os.getenv(name, "").strip()
        if configured:
            signals.append((name, configured))

    arguments = sys.argv[1:]
    for index, argument in enumerate(arguments):
        if argument == "--workers":
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
                f"{source} must be 1 while calibration and Master Suite workflow state is process-local"
            )
    return 1


def main() -> None:
    prepare_multiprocess_metrics()
    uvicorn.run(
        "app.api:app",
        host=os.getenv("GDW_HOST", "0.0.0.0"),
        port=int(os.getenv("GDW_PORT", "8080")),
        workers=governed_worker_count(),
        access_log=False,
    )


if __name__ == "__main__":
    main()
