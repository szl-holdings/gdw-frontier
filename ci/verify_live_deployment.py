from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.source_identity import source_tree_digest  # noqa: E402
from ci.receipt_proof import verify_signed_receipt  # noqa: E402

DEFAULT_COMPOSE_FILE = ROOT / "monitoring" / "docker-compose.yml"
DEFAULT_OUTPUT = ROOT / "output" / "proofs" / "live-deployment.json"
VERIFIER_USER_AGENT = "GDW-Deployment-Verifier/0.1"
OCI_REVISION_LABEL = "org.opencontainers.image.revision"


def request_json(
    url: str,
    *,
    token: str | None = None,
    method: str = "GET",
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    headers = {"Accept": "application/json", "User-Agent": VERIFIER_USER_AGENT}
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
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": VERIFIER_USER_AGENT},
    )
    try:
        urllib.request.urlopen(request, timeout=15)
    except urllib.error.HTTPError as error:
        assert error.code == 401
        assert error.headers.get("WWW-Authenticate") == "Bearer"
        return {"status": 401, "challenge": "Bearer"}
    raise AssertionError(f"{url} unexpectedly allowed an unauthenticated request")


def expect_http_status(url: str, expected_status: int) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={"Accept": "*/*", "User-Agent": VERIFIER_USER_AGENT},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            observed_status = response.status
    except urllib.error.HTTPError as error:
        observed_status = error.code
    assert observed_status == expected_status, (
        f"{url} returned {observed_status}; expected {expected_status}"
    )
    return {"status": observed_status}


def assert_checkout_source(source_sha: str) -> dict[str, Any]:
    assert re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", source_sha), (
        "--expected-source must be a full lowercase Git object ID"
    )
    checkout_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        text=True,
    ).strip()
    checkout_status = subprocess.check_output(
        ["git", "status", "--porcelain=v1"],
        cwd=ROOT,
        text=True,
    ).strip()
    assert checkout_sha == source_sha, (
        f"checked-out source is {checkout_sha}, not expected source {source_sha}"
    )
    assert not checkout_status, (
        "the deployment checkout has tracked or untracked changes"
    )
    return {
        "commit": checkout_sha,
        "clean": True,
        "source_tree_sha256": source_tree_digest(ROOT),
    }


def assert_identity(payload: dict[str, Any], source_sha: str, image_digest: str) -> dict[str, Any]:
    assert payload["service"] == "gdw"
    assert payload["source_sha"] == source_sha
    assert payload["image_digest"] == image_digest
    assert payload["deployment_mode"] == "SINGLE_NODE_LIVE"
    assert payload["model_status"] == "MODELED"
    assert payload["receipt_signing"] == "ECDSA_P256_SHA256"
    assert payload["receipt_signing_key_id"].startswith("sha256:")
    return payload


def assert_image_source(
    container: dict[str, Any],
    source_sha: str,
    checkout_source_tree: str,
    image_source_tree: str,
) -> dict[str, Any]:
    labels = container.get("Config", {}).get("Labels") or {}
    image_source_sha = labels.get(OCI_REVISION_LABEL)
    assert image_source_sha == source_sha, (
        f"container image revision is {image_source_sha!r}, not expected source {source_sha}"
    )
    assert image_source_tree == checkout_source_tree, (
        "the running image source tree does not match the clean deployment checkout"
    )
    return {
        "label": OCI_REVISION_LABEL,
        "commit": image_source_sha,
        "source_tree_sha256": image_source_tree,
    }


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
        "--api-token-file",
        type=Path,
        default=os.getenv("GDW_API_TOKEN_SOURCE_FILE"),
        help="Protected file containing the bearer token used by deployment checks.",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Optional Compose env file. If omitted, Compose inherits the verifier environment.",
    )
    parser.add_argument("--compose-file", type=Path, default=DEFAULT_COMPOSE_FILE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    if args.api_token_file is None:
        raise SystemExit("--api-token-file or GDW_API_TOKEN_SOURCE_FILE must point to the deployment token file")
    token = args.api_token_file.read_text(encoding="utf-8").strip()
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
            "cryptographic_receipt_signing": "VERIFIED_FILE_BACKED_P256",
            "external_witness": "UNAVAILABLE",
            "off_host_backup": "UNVERIFIED",
        },
        "source_sha": args.expected_source,
        "image_digest": args.expected_image,
        "public_url": base_url,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "checks": {},
    }

    checkout_source = assert_checkout_source(args.expected_source)
    proof["checks"]["checkout_source"] = checkout_source
    proof["checks"]["public_health"] = wait_for(
        "public health",
        lambda: assert_ready(request_json(f"{base_url}/healthz")),
    )
    proof["checks"]["public_readiness"] = wait_for(
        "public readiness",
        lambda: assert_ready(request_json(f"{base_url}/readyz")),
    )
    proof["checks"]["public_metrics_denied"] = wait_for(
        "public metrics denied at ingress",
        lambda: expect_http_status(f"{base_url}/metrics", 404),
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
    assert ledger_before["signature_status"] == "SIGNED"
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
    assert ledger_written["signature_status"] == "SIGNED"
    assert ledger_written["records"] == ledger_before["records"] + 1
    assert ledger_written["head_hash"] == step["receipt_hash"]
    signed_receipt = verify_signed_receipt(
        request_json(f"{base_url}/v1/gdw/receipt", token=token)
    )
    assert signed_receipt["receipt_hash"] == step["receipt_hash"]
    assert signed_receipt["body"]["runtime_identity"]["source_sha"] == args.expected_source
    assert (
        signed_receipt["body"]["runtime_identity"]["image_digest"]
        == args.expected_image
    )
    proof["checks"]["cryptographic_receipt"] = signed_receipt

    backup = request_json(
        f"{base_url}/v1/gdw/backup",
        token=token,
        method="POST",
    )
    assert backup["status"] == "VERIFIED"
    assert backup["head_hash"] == step["receipt_hash"]
    restore_output = subprocess.check_output(
        compose_command(
            args.compose_file,
            args.env_file,
            "run",
            "--rm",
            "backup-restore-verify",
        ),
        cwd=ROOT,
        text=True,
    )
    restore_proof = json.loads(restore_output.strip().splitlines()[-1])
    assert restore_proof["status"] == "VERIFIED"
    assert restore_proof["head_hash"] == backup["head_hash"]
    assert restore_proof["records"] == backup["records"]
    proof["checks"]["backup_restore"] = {
        "backup": backup,
        "restore": restore_proof,
        "scope": "separate local Docker volume restored into isolated tmpfs",
    }

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
        "signature_status": ledger_recovered["signature_status"],
        "signing_key_id": ledger_recovered["signing_key_id"],
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
    image_source_tree = subprocess.check_output(
        ["docker", "exec", container_id, "python", "-m", "app.source_identity"],
        cwd=ROOT,
        text=True,
    ).strip()
    proof["checks"]["image_source"] = assert_image_source(
        container,
        args.expected_source,
        checkout_source["source_tree_sha256"],
        image_source_tree,
    )
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
