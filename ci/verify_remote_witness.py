from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ci.receipt_proof import verify_signed_receipt  # noqa: E402
from ci.verify_live_deployment import (  # noqa: E402
    assert_identity,
    assert_ready,
    expect_http_status,
    expect_unauthorized,
    request_json,
)

DEFAULT_OUTPUT = ROOT / "output" / "proofs" / "remote-production-witness.json"
SOURCE_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
IMAGE_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


def verifier_identity() -> dict[str, str]:
    server = os.getenv("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    repository = os.getenv("GITHUB_REPOSITORY", "UNAVAILABLE")
    run_id = os.getenv("GITHUB_RUN_ID", "UNAVAILABLE")
    run_url = (
        f"{server}/{repository}/actions/runs/{run_id}"
        if repository != "UNAVAILABLE" and run_id != "UNAVAILABLE"
        else "UNAVAILABLE"
    )
    return {
        "repository": repository,
        "workflow_source_sha": os.getenv("GITHUB_SHA", "UNAVAILABLE"),
        "run_id": run_id,
        "run_attempt": os.getenv("GITHUB_RUN_ATTEMPT", "UNAVAILABLE"),
        "run_url": run_url,
    }


def collect_remote_witness(
    *,
    public_url: str,
    expected_source: str,
    expected_image: str,
    token: str,
) -> dict[str, Any]:
    if not public_url.startswith("https://"):
        raise ValueError("--public-url must use HTTPS")
    if not SOURCE_SHA_PATTERN.fullmatch(expected_source):
        raise ValueError("--expected-source must be a full lowercase Git SHA")
    if not IMAGE_DIGEST_PATTERN.fullmatch(expected_image):
        raise ValueError("--expected-image must be a full lowercase sha256 image ID")
    if len(token) < 32:
        raise ValueError("the deployment token must contain at least 32 characters")

    base_url = public_url.rstrip("/")
    health = assert_ready(request_json(f"{base_url}/healthz"))
    readiness = assert_ready(request_json(f"{base_url}/readyz"))
    metrics = expect_http_status(f"{base_url}/metrics", 404)
    identity = assert_identity(
        request_json(f"{base_url}/build-info"),
        expected_source,
        expected_image,
    )
    unauthenticated = expect_unauthorized(f"{base_url}/v1/gdw/ledger")
    ledger = request_json(f"{base_url}/v1/gdw/ledger", token=token)
    signed_receipt = verify_signed_receipt(
        request_json(f"{base_url}/v1/gdw/receipt", token=token)
    )

    assert ledger["valid"] is True
    assert ledger["records"] > 0
    assert ledger["signature_status"] == "SIGNED"
    assert ledger["head_hash"] == signed_receipt["receipt_hash"]
    assert ledger["signing_key_id"] == signed_receipt["signing_key_id"]
    assert identity["receipt_signing_key_id"] == signed_receipt["signing_key_id"]
    runtime_identity = signed_receipt["body"]["runtime_identity"]
    assert runtime_identity["source_sha"] == expected_source
    assert runtime_identity["image_digest"] == expected_image

    return {
        "schema_version": 1,
        "status": "VERIFIED",
        "scope": "read-only independent public deployment witness",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "public_url": base_url,
        "expected_source": expected_source,
        "expected_image": expected_image,
        "verifier": verifier_identity(),
        "truth_boundary": {
            "single_node_public_operation": "VERIFIED",
            "cryptographic_receipt_signing": "VERIFIED_FILE_BACKED_P256",
            "external_witness": "VERIFIED",
            "high_availability": "UNVERIFIED",
            "multi_node_durability": "UNVERIFIED",
            "off_host_disaster_recovery": "UNVERIFIED",
            "hardware_backed_signing": "UNAVAILABLE",
        },
        "checks": {
            "public_health": health,
            "public_readiness": readiness,
            "public_metrics_denied": metrics,
            "source_identity": identity,
            "unauthenticated_operator_access": unauthenticated,
            "signed_ledger": {
                "valid": True,
                "records": ledger["records"],
                "head_hash": ledger["head_hash"],
                "signature_status": ledger["signature_status"],
                "signing_key_id": ledger["signing_key_id"],
            },
            "cryptographic_receipt": signed_receipt,
        },
    }


def write_proof(path: Path, proof: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(proof, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify GDW from an independent read-only public witness."
    )
    parser.add_argument("--public-url", required=True)
    parser.add_argument("--expected-source", required=True)
    parser.add_argument("--expected-image", required=True)
    parser.add_argument(
        "--api-token-file",
        type=Path,
        default=os.getenv("GDW_API_TOKEN_SOURCE_FILE"),
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    if args.api_token_file is None:
        raise SystemExit(
            "--api-token-file or GDW_API_TOKEN_SOURCE_FILE must point to the deployment token file"
        )
    token = args.api_token_file.read_text(encoding="utf-8").strip()
    try:
        proof = collect_remote_witness(
            public_url=args.public_url,
            expected_source=args.expected_source,
            expected_image=args.expected_image,
            token=token,
        )
    except Exception as error:
        write_proof(
            args.output,
            {
                "schema_version": 1,
                "status": "UNVERIFIED",
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "verifier": verifier_identity(),
                "failure": {
                    "type": type(error).__name__,
                    "message": str(error),
                },
            },
        )
        raise
    write_proof(args.output, proof)
    print(json.dumps(proof, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
