from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from ci import verify_remote_witness

SOURCE_SHA = "a" * 40
IMAGE_DIGEST = f"sha256:{'b' * 64}"
TOKEN = "remote-witness-test-token-with-32-characters"
KEY_ID = f"sha256:{'c' * 64}"
RECEIPT_HASH = "d" * 64
WORKFLOW_PATH = (
    verify_remote_witness.ROOT
    / ".github"
    / "workflows"
    / "gdw-production-witness.yml"
)


class UniqueKeyLoader(yaml.BaseLoader):
    """Keep GitHub's `on` key literal and reject ambiguous workflow mappings."""


def _construct_unique_mapping(
    loader: UniqueKeyLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict:
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise ValueError(f"duplicate workflow key: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _load_workflow(path: Path = WORKFLOW_PATH) -> dict:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueKeyLoader)


def test_production_witness_executes_only_trusted_default_branch_code() -> None:
    workflow = _load_workflow()

    assert workflow["on"] == {
        "repository_dispatch": {"types": ["gdw-production-witness"]}
    }
    assert "workflow_dispatch" not in workflow["on"]
    assert workflow["permissions"] == {"contents": "read"}
    assert set(workflow["jobs"]) == {"witness"}

    witness = workflow["jobs"]["witness"]
    condition = " ".join(witness["if"].split())
    assert condition == (
        "github.event_name == 'repository_dispatch' && "
        "github.event.action == 'gdw-production-witness' && "
        "github.repository == 'szl-holdings/gdw-frontier' && "
        "github.event.repository.full_name == 'szl-holdings/gdw-frontier' && "
        "github.ref == format('refs/heads/{0}', "
        "github.event.repository.default_branch)"
    )
    assert witness["environment"] == "production"

    steps = witness["steps"]
    assert [step.get("name", step.get("uses")) for step in steps] == [
        "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
        "Verify trusted default-branch workflow source",
        "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97",
        "Install verifier",
        "Materialize protected witness credential",
        "Verify exact public production identity and signed receipt",
        "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
    ]
    checkout = steps[0]
    assert checkout == {
        "uses": "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
        "with": {
            "fetch-depth": "1",
            "persist-credentials": "false",
            "ref": "${{ github.sha }}",
        },
    }

    identity = steps[1]
    assert identity["name"] == "Verify trusted default-branch workflow source"
    assert identity["env"] == {
        "DEFAULT_BRANCH": "${{ github.event.repository.default_branch }}",
        "EVENT_ACTION": "${{ github.event.action }}",
        "EVENT_REPOSITORY": "${{ github.event.repository.full_name }}",
        "EXPECTED_REPOSITORY": "szl-holdings/gdw-frontier",
        "OBSERVED_REF": "${{ github.ref }}",
        "OBSERVED_REPOSITORY": "${{ github.repository }}",
        "OBSERVED_SHA": "${{ github.sha }}",
    }
    assert identity["run"].splitlines() == [
        "set -euo pipefail",
        'test "$GITHUB_EVENT_NAME" = "repository_dispatch"',
        'test "$EVENT_ACTION" = "gdw-production-witness"',
        'test "$EXPECTED_REPOSITORY" = "$OBSERVED_REPOSITORY"',
        'test "$EXPECTED_REPOSITORY" = "$EVENT_REPOSITORY"',
        'test "$OBSERVED_REF" = "refs/heads/$DEFAULT_BRANCH"',
        'test "$(git rev-parse HEAD)" = "$OBSERVED_SHA"',
        'test -z "$(git status --porcelain=v1)"',
    ]

    secret_step = next(
        index
        for index, step in enumerate(steps)
        if step.get("name") == "Materialize protected witness credential"
    )
    assert secret_step > 1

    secret_references: list[tuple[tuple[object, ...], str]] = []

    def collect_secret_references(value: object, path: tuple[object, ...] = ()) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                collect_secret_references(child, (*path, key))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                collect_secret_references(child, (*path, index))
        elif isinstance(value, str) and "secrets." in value:
            secret_references.append((path, value))

    collect_secret_references(workflow)
    assert secret_references == [
        (
            (
                "jobs",
                "witness",
                "steps",
                secret_step,
                "env",
                "GDW_PRODUCTION_API_TOKEN",
            ),
            "${{ secrets.GDW_PRODUCTION_API_TOKEN }}",
        )
    ]
    workflow_text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "client_payload" not in workflow_text


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
