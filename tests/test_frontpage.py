"""The public front page serves at "/" without bearer auth (szl.frontpage/v1).

Evidence: "/" is in AUTH_EXEMPT_PATHS on main but previously had no handler,
so the landing route answered 404. These tests pin the corrected behavior
and confirm the governed API surface stays fail-closed.
"""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app import api


def test_root_serves_responsive_front_page(tmp_path: Path) -> None:
    api.ledger.path = tmp_path / "receipts.jsonl"
    api.exemplar_store.path = tmp_path / "exemplars.jsonl"
    with TestClient(api.app) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert 'name="viewport"' in response.text
        assert "GDW-X" in response.text
        assert "/healthz" in response.text


def test_auth_still_enforced_on_governed_surface(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("GDW_REQUIRE_AUTH", "1")
    monkeypatch.setenv("GDW_API_TOKEN", "test-token")
    api.ledger.path = tmp_path / "receipts.jsonl"
    api.exemplar_store.path = tmp_path / "exemplars.jsonl"
    with TestClient(api.app) as client:
        rejected = client.get("/v1/gdw/state")
        assert rejected.status_code == 401
        front = client.get("/")
        assert front.status_code == 200
