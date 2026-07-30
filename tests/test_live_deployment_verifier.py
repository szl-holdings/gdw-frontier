from __future__ import annotations

import json

from ci import verify_live_deployment


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
