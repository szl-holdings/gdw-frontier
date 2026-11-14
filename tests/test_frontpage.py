"""The public front page (szl.frontpage/v1) is visible without bearer auth.

The landing route "/" already serves the public JSON service banner, so the
front page attaches at "/front" (non-shadow doctrine). These tests pin that
"/front" is auth-exempt and that the governed API surface stays fail-closed.
"""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app import api

LONG_TOKEN = "test-token-that-is-at-least-32-characters"


def test_front_page_visible_without_bearer_auth(tmp_path: Path) -> None:
    api.ledger.path = tmp_path / "receipts.jsonl"
    api.exemplar_store.path = tmp_path / "exemplars.jsonl"
    with TestClient(api.app) as client:
        response = client.get("/front")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert 'name="viewport"' in response.text
        assert "GDW-X" in response.text
        assert "/healthz" in response.text


def test_auth_still_enforced_on_governed_surface(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("GDW_REQUIRE_AUTH", "1")
    monkeypatch.setenv("GDW_API_TOKEN", LONG_TOKEN)
    api.ledger.path = tmp_path / "receipts.jsonl"
    api.exemplar_store.path = tmp_path / "exemplars.jsonl"
    with TestClient(api.app) as client:
        rejected = client.get("/v1/gdw/state")
        assert rejected.status_code == 401
        front = client.get("/front")
        assert front.status_code == 200
