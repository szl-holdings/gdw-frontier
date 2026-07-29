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


def main() -> None:
    prepare_multiprocess_metrics()
    uvicorn.run(
        "app.api:app",
        host=os.getenv("GDW_HOST", "0.0.0.0"),
        port=int(os.getenv("GDW_PORT", "8080")),
        workers=int(os.getenv("GDW_WORKERS", "1")),
        access_log=False,
    )


if __name__ == "__main__":
    main()
