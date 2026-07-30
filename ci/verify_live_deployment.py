from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COMPOSE_FILE = ROOT / "monitoring" / "docker-compose.yml"
DEFAULT_OUTPUT = ROOT / "output" / "proofs" / "live-deployment.json"


def request_json(
    url: str,
    *,
    token: str | None = None,
    method: str = "GET",
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def wait_for(name: str, check: Callable[[], Any], *, timeout: float = 120.0) -> Any:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            result = check()
            print(f"verified: {name}")
            return result
        except (AssertionError, OSError, urllib.error.URLError, json.JSONDecodeError) as error:
            last_error = error
            time.sleep(2)
    raise TimeoutError(f"{name} was not verified within {timeout}s: {last_error}")


def expect_unauthorized(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        urllib.request.urlopen(request, timeout=15)
    except urllib.error.HTTPError as error:
        assert error.code == 401
        assert error.headers.get("WWW-Authenticate") == "Bearer"
        return {"status": 401, "challenge": "Bearer"}
    raise AssertionError(f"{url} unexpectedly allowed an unauthenticated request")


def assert_identity(payload: dict[str, Any], source_sha: str, image_digest: str) -> dict[str, Any]:
    assert payload["service"] == "gdw"
    assert payload["source_sha"] == source_sha
    assert payload["image_digest"] == image_digest
    assert payload["deployment_mode"] == "SINGLE_NODE_LIVE"
    assert payload["model_status"] == "MODELED"
    return payload


def assert_ready(payload: dict[str, Any]) -> dict[str, Any]:
    assert payload == {"status": "ready", "service": "gdw", "model_status": "MODELED"}
    return payload


def compose_command(compose_file: Path, env_file: Path | None, *args: str) -> list[str]:
    command = ["docker", "compose"]
    if env_file is not None:
        command.extend(["--env-file", str(env_file)])
    command.extend(["-f", str(compose_file), *args])
    return command


def inspect_container(container_id: str) -> dict[str, Any]:
    return json.loads(
        subprocess.check_output(["docker", "inspect", container_id], cwd=ROOT, text=True)
    )[0]


def assert_container_healthy(container_id: str) -> dict[str, Any]:
    container = inspect_container(container_id)
    assert container["State"]["Health"]["Status"] == "healthy"
    return container


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify a credential-backed GDW single-node deployment.")
    parser.add_argument("--public-url", required=True)
    parser.add_argument("--expected-source", required=True)
    parser.add_argument("--expected-image", required=True)
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Optional Compose env file. If omitted, Compose inherits the verifier environment.",
    )
    parser.add_argument("--compose-file", type=Path, default=DEFAULT_COMPOSE_FILE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    token_file = os.getenv("GDW_API_TOKEN_SOURCE_FILE", "")
    if not token_file:
        raise SystemExit("GDW_API_TOKEN_SOURCE_FILE must point to the deployment token file")
    token = Path(token_file).read_text(encoding="utf-8").strip()
    if len(token) < 32:
        raise SystemExit("the deployment token file must contain at least 32 characters")
    if not args.public_url.startswith("https://"):
        raise SystemExit("--public-url must use HTTPS")

    base_url = args.public_url.rstrip("/")
    proof: dict[str, Any] = {
        "status": "UNVERIFIED",
        "scope": "credential-backed single-node Docker Compose deployment",
        "truth_boundary": {
            "model_status": "MODELED",
            "high_availability": "UNVERIFIED",
            "multi_node_durability": "UNVERIFIED",
            "cryptographic_receipt_signing": "UNAVAILABLE",
        },
        "source_sha": args.expected_source,
        "image_digest": args.expected_image,
        "public_url": base_url,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "checks": {},
    }

    proof["checks"]["public_health"] = wait_for(
        "public health",
        lambda: assert_ready(request_json(f"{base_url}/healthz")),
    )
    proof["checks"]["public_readiness"] = wait_for(
        "public readiness",
        lambda: assert_ready(request_json(f"{base_url}/readyz")),
    )
    proof["checks"]["source_identity"] = wait_for(
        "exact source and image identity",
        lambda: assert_identity(
            request_json(f"{base_url}/build-info"),
            args.expected_source,
            args.expected_image,
        ),
    )
    proof["checks"]["unauthenticated_operator_access"] = expect_unauthorized(f"{base_url}/v1/gdw/ledger")

    ledger_before = request_json(f"{base_url}/v1/gdw/ledger", token=token)
    assert ledger_before["valid"] is True
    step = request_json(
        f"{base_url}/v1/gdw/step",
        token=token,
        method="POST",
        body={
            "session_id": f"live-verification-{uuid.uuid4().hex}",
            "proposal_id": f"restart-proof-{args.expected_source}",
            "objective": "verify credentialed ingress and persistent receipt-chain recovery",
            "confidence": 0.92,
            "receipt_present": True,
            "scheduler_mode": "balanced",
        },
    )
    assert step["status"] == "MODELED"
    assert step["receipt_status"] == "valid"
    ledger_written = request_json(f"{base_url}/v1/gdw/ledger", token=token)
    assert ledger_written["valid"] is True
    assert ledger_written["records"] == ledger_before["records"] + 1
    assert ledger_written["head_hash"] == step["receipt_hash"]

    subprocess.run(
        compose_command(args.compose_file, args.env_file, "restart", "app"),
        cwd=ROOT,
        check=True,
    )
    wait_for(
        "readiness after container restart",
        lambda: assert_ready(request_json(f"{base_url}/readyz")),
    )
    ledger_recovered = wait_for(
        "ledger recovery after container restart",
        lambda: request_json(f"{base_url}/v1/gdw/ledger", token=token),
    )
    assert ledger_recovered == ledger_written
    proof["checks"]["restart_durability"] = {
        "valid": True,
        "records_before": ledger_before["records"],
        "records_after": ledger_recovered["records"],
        "head_hash": ledger_recovered["head_hash"],
        "receipt_hash": step["receipt_hash"],
    }

    container_id = subprocess.check_output(
        compose_command(args.compose_file, args.env_file, "ps", "-q", "app"),
        cwd=ROOT,
        text=True,
    ).strip()
    assert container_id
    container = wait_for(
        "container health after restart",
        lambda: assert_container_healthy(container_id),
    )
    assert container["Image"] == args.expected_image
    assert container["HostConfig"]["ReadonlyRootfs"] is True
    assert "ALL" in container["HostConfig"]["CapDrop"]
    assert container["HostConfig"]["RestartPolicy"]["Name"] == "unless-stopped"
    proof["checks"]["container_controls"] = {
        "container_id": container_id,
        "health": container["State"]["Health"]["Status"],
        "read_only_root": True,
        "capabilities_dropped": ["ALL"],
        "restart_policy": "unless-stopped",
    }

    proof["status"] = "VERIFIED"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(proof, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
