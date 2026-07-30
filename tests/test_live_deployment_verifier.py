from __future__ import annotations

import json
import subprocess
import sys
import urllib.error

import pytest

from ci import verify_live_deployment


def test_documented_direct_invocation_loads_from_fresh_checkout(tmp_path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            str(verify_live_deployment.ROOT / "ci" / "verify_live_deployment.py"),
            "--help",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "Verify a credential-backed GDW single-node deployment" in result.stdout


class FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self) -> bytes:
        return json.dumps({"status": "ready"}).encode("utf-8")


def test_request_json_sends_explicit_verifier_identity(monkeypatch) -> None:
    observed = {}

    def fake_urlopen(request, timeout):
        observed["user_agent"] = request.get_header("User-agent")
        observed["accept"] = request.get_header("Accept")
        observed["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(verify_live_deployment.urllib.request, "urlopen", fake_urlopen)

    result = verify_live_deployment.request_json("https://gdw.example.test/healthz")

    assert result == {"status": "ready"}
    assert observed == {
        "user_agent": verify_live_deployment.VERIFIER_USER_AGENT,
        "accept": "application/json",
        "timeout": 15,
    }


SOURCE_SHA = "a" * 40


def test_checkout_source_requires_exact_clean_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outputs = iter((f"{SOURCE_SHA}\n", ""))
    monkeypatch.setattr(
        verify_live_deployment.subprocess,
        "check_output",
        lambda *args, **kwargs: next(outputs),
    )

    assert verify_live_deployment.assert_checkout_source(SOURCE_SHA) == {
        "commit": SOURCE_SHA,
        "clean": True,
        "source_tree_sha256": verify_live_deployment.source_tree_digest(
            verify_live_deployment.ROOT
        ),
    }


def test_checkout_source_rejects_dirty_tree(monkeypatch: pytest.MonkeyPatch) -> None:
    outputs = iter((f"{SOURCE_SHA}\n", " M Dockerfile\n"))
    monkeypatch.setattr(
        verify_live_deployment.subprocess,
        "check_output",
        lambda *args, **kwargs: next(outputs),
    )

    with pytest.raises(AssertionError, match="tracked or untracked changes"):
        verify_live_deployment.assert_checkout_source(SOURCE_SHA)


def test_image_source_requires_matching_oci_revision() -> None:
    source_tree = "c" * 64
    container = {
        "Config": {
            "Labels": {
                verify_live_deployment.OCI_REVISION_LABEL: SOURCE_SHA,
            }
        }
    }

    assert verify_live_deployment.assert_image_source(
        container,
        SOURCE_SHA,
        source_tree,
        source_tree,
    ) == {
        "label": verify_live_deployment.OCI_REVISION_LABEL,
        "commit": SOURCE_SHA,
        "source_tree_sha256": source_tree,
    }

    with pytest.raises(AssertionError, match="container image revision"):
        verify_live_deployment.assert_image_source(
            container,
            "b" * 40,
            source_tree,
            source_tree,
        )

    with pytest.raises(AssertionError, match="source tree"):
        verify_live_deployment.assert_image_source(
            container,
            SOURCE_SHA,
            source_tree,
            "d" * 64,
        )


def test_public_metrics_must_return_configured_denial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    denied = urllib.error.HTTPError(
        "https://gdw.example.com/metrics",
        404,
        "Not Found",
        {},
        None,
    )
    monkeypatch.setattr(
        verify_live_deployment.urllib.request,
        "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(denied),
    )

    assert verify_live_deployment.expect_http_status(
        "https://gdw.example.com/metrics",
        404,
    ) == {"status": 404}


def test_public_metrics_rejects_origin_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class PublicMetricsResponse:
        status = 200

        def __enter__(self) -> PublicMetricsResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(
        verify_live_deployment.urllib.request,
        "urlopen",
        lambda *args, **kwargs: PublicMetricsResponse(),
    )

    with pytest.raises(AssertionError, match="returned 200; expected 404"):
        verify_live_deployment.expect_http_status(
            "https://gdw.example.com/metrics",
            404,
        )
