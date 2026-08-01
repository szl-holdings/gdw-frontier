from __future__ import annotations

import os
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
    configured = os.getenv("GDW_WORKERS", "1")
    try:
        workers = int(configured)
    except ValueError as error:
        raise RuntimeError("GDW_WORKERS must be the integer 1") from error
    if workers != 1:
        raise RuntimeError(
            "GDW_WORKERS must be 1 while Master Suite state is process-local"
        )
    return workers


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
