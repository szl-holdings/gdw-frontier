from __future__ import annotations

import json
import subprocess
import sys

import pytest

from ci import verify_remote_witness

SOURCE_SHA = "a" * 40
IMAGE_DIGEST = f"sha256:{'b' * 64}"
TOKEN = "remote-witness-test-token-with-32-characters"
KEY_ID = f"sha256:{'c' * 64}"
RECEIPT_HASH = "d" * 64


def test_documented_direct_invocation_loads_from_fresh_checkout(tmp_path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            str(verify_remote_witness.ROOT / "ci" / "verify_remote_witness.py"),
            "--help",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "independent read-only public witness" in result.stdout


def test_remote_witness_is_read_only_and_excludes_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, bool]] = []

    def fake_request_json(url: str, *, token: str | None = None) -> dict:
        calls.append((url, token is not None))
        if url.endswith("/healthz") or url.endswith("/readyz"):
            return {"status": "ready", "service": "gdw", "model_status": "MODELED"}
        if url.endswith("/build-info"):
            return {
                "service": "gdw",
                "source_sha": SOURCE_SHA,
                "image_digest": IMAGE_DIGEST,
                "deployment_mode": "SINGLE_NODE_LIVE",
                "model_status": "MODELED",
                "receipt_signing": "ECDSA_P256_SHA256",
                "receipt_signing_key_id": KEY_ID,
            }
        if url.endswith("/v1/gdw/ledger"):
            return {
                "valid": True,
                "records": 10,
                "head_hash": RECEIPT_HASH,
                "signature_status": "SIGNED",
                "signing_key_id": KEY_ID,
            }
        if url.endswith("/v1/gdw/receipt"):
            return {"raw": "receipt"}
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(verify_remote_witness, "request_json", fake_request_json)
    monkeypatch.setattr(
        verify_remote_witness,
        "expect_http_status",
        lambda url, status: {"status": status},
    )
    monkeypatch.setattr(
        verify_remote_witness,
        "expect_unauthorized",
        lambda url: {"status": 401, "challenge": "Bearer"},
    )
    monkeypatch.setattr(
        verify_remote_witness,
        "verify_signed_receipt",
        lambda proof: {
            "valid": True,
            "receipt_hash": RECEIPT_HASH,
            "previous_hash": "e" * 64,
            "signature_algorithm": "ECDSA_P256_SHA256",
            "signing_key_id": KEY_ID,
            "body": {
                "runtime_identity": {
                    "source_sha": SOURCE_SHA,
                    "image_digest": IMAGE_DIGEST,
                }
            },
        },
    )

    proof = verify_remote_witness.collect_remote_witness(
        public_url="https://gdw.example.test",
        expected_source=SOURCE_SHA,
        expected_image=IMAGE_DIGEST,
        token=TOKEN,
    )

    assert proof["status"] == "VERIFIED"
    assert proof["truth_boundary"]["external_witness"] == "VERIFIED"
    assert TOKEN not in json.dumps(proof)
    assert calls == [
        ("https://gdw.example.test/healthz", False),
        ("https://gdw.example.test/readyz", False),
        ("https://gdw.example.test/build-info", False),
        ("https://gdw.example.test/v1/gdw/ledger", True),
        ("https://gdw.example.test/v1/gdw/receipt", True),
    ]


def test_remote_witness_rejects_non_https_target() -> None:
    with pytest.raises(ValueError, match="must use HTTPS"):
        verify_remote_witness.collect_remote_witness(
            public_url="http://gdw.example.test",
            expected_source=SOURCE_SHA,
            expected_image=IMAGE_DIGEST,
            token=TOKEN,
        )
