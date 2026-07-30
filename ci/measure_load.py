from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import time
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

USER_AGENT = "GDW-Load-Measurer/0.1"


def request_json(
    url: str,
    *,
    token: str,
    method: str = "GET",
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
        "User-Agent": USER_AGENT,
    }
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def percentile(values: list[float], probability: float) -> float:
    if not values:
        raise ValueError("cannot calculate a percentile from an empty sample")
    ordered = sorted(values)
    index = max(0, math.ceil(probability * len(ordered)) - 1)
    return ordered[index]


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure bounded authenticated GDW write load.")
    parser.add_argument("--public-url", required=True)
    parser.add_argument("--api-token-file", type=Path, required=True)
    parser.add_argument("--requests", type=int, default=200)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-http-loopback", action="store_true")
    args = parser.parse_args(argv)

    if args.requests < 1 or args.requests > 10_000:
        raise SystemExit("--requests must be between 1 and 10000")
    if args.concurrency < 1 or args.concurrency > 128:
        raise SystemExit("--concurrency must be between 1 and 128")
    base_url = args.public_url.rstrip("/")
    if not base_url.startswith("https://"):
        loopback = base_url.startswith("http://127.0.0.1") or base_url.startswith(
            "http://localhost"
        )
        if not args.allow_http_loopback or not loopback:
            raise SystemExit("non-loopback targets must use HTTPS")
    token = args.api_token_file.read_text(encoding="utf-8").strip()
    if len(token) < 32:
        raise SystemExit("the API token file must contain at least 32 characters")

    ledger_before = request_json(f"{base_url}/v1/gdw/ledger", token=token)
    if ledger_before.get("valid") is not True:
        raise SystemExit("the ledger was invalid before the load measurement")
    run_id = uuid.uuid4().hex

    def execute(index: int) -> tuple[float, dict[str, Any]]:
        started = time.perf_counter()
        response = request_json(
            f"{base_url}/v1/gdw/step",
            token=token,
            method="POST",
            body={
                "session_id": f"load-{run_id}-{index}",
                "proposal_id": f"load-proposal-{run_id}-{index}",
                "objective": "measure bounded authenticated single-node receipt throughput",
                "confidence": 0.91,
                "receipt_present": True,
                "scheduler_mode": "throughput",
            },
        )
        return time.perf_counter() - started, response

    started = time.perf_counter()
    results: list[tuple[float, dict[str, Any]]] = []
    errors: list[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = [executor.submit(execute, index) for index in range(args.requests)]
        for future in concurrent.futures.as_completed(futures):
            try:
                results.append(future.result())
            except Exception as error:
                errors.append(f"{type(error).__name__}: {error}")
    duration = time.perf_counter() - started
    ledger_after = request_json(f"{base_url}/v1/gdw/ledger", token=token)

    latencies = [latency for latency, _ in results]
    responses = [response for _, response in results]
    receipt_hashes = {
        str(response.get("receipt_hash"))
        for response in responses
        if response.get("receipt_hash")
    }
    expected_after = int(ledger_before["records"]) + args.requests
    valid = (
        not errors
        and len(results) == args.requests
        and all(response.get("status") == "MODELED" for response in responses)
        and all(response.get("receipt_status") == "valid" for response in responses)
        and len(receipt_hashes) == args.requests
        and ledger_after.get("valid") is True
        and int(ledger_after["records"]) == expected_after
        and ledger_after.get("signature_status") == "SIGNED"
    )
    proof = {
        "status": "VERIFIED" if valid else "FAILED",
        "scope": "bounded authenticated single-node write measurement",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "target": base_url,
        "requests": args.requests,
        "concurrency": args.concurrency,
        "successful": len(results),
        "errors": errors,
        "duration_seconds": round(duration, 6),
        "throughput_requests_per_second": round(len(results) / duration, 3),
        "latency_ms": {
            "p50": round(percentile(latencies, 0.50) * 1000, 3) if latencies else None,
            "p95": round(percentile(latencies, 0.95) * 1000, 3) if latencies else None,
            "p99": round(percentile(latencies, 0.99) * 1000, 3) if latencies else None,
            "max": round(max(latencies) * 1000, 3) if latencies else None,
        },
        "ledger": {
            "records_before": ledger_before["records"],
            "records_after": ledger_after["records"],
            "head_hash": ledger_after["head_hash"],
            "signature_status": ledger_after["signature_status"],
            "signing_key_id": ledger_after["signing_key_id"],
        },
        "truth_boundary": {
            "high_availability": "UNVERIFIED",
            "multi_node_durability": "UNVERIFIED",
            "tenant_isolation": "UNVERIFIED",
            "production_scale": "UNVERIFIED",
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(proof, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(proof, indent=2, sort_keys=True))
    if not valid:
        raise SystemExit("bounded load measurement failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
