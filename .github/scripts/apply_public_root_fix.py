#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Apply the reviewed GDW public-root operability repair.

The mutation is deliberately exact: expose only `/` as a read-only service
landing while preserving bearer authentication on every operator endpoint. The
new route and its GET/HEAD methods are added to the bounded metric-cardinality
policy rather than bypassing observability governance.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API = ROOT / "app" / "api.py"
TESTS = ROOT / "tests" / "test_api.py"
POLICY = ROOT / "monitoring" / "metric-policy.json"

AUTH_OLD = '''AUTH_EXEMPT_PATHS = frozenset(
    {
        "/build-info",'''
AUTH_NEW = '''AUTH_EXEMPT_PATHS = frozenset(
    {
        "/",
        "/build-info",'''

ROUTE_ANCHOR = '''@app.get("/healthz", response_model=ReadinessResponse)
async def healthz() -> ReadinessResponse:
'''
ROUTE_BLOCK = '''@app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
async def public_landing() -> JSONResponse:
    """Public, read-only service landing; operator APIs remain bearer-protected."""
    return JSONResponse(
        {
            "service": "gdw",
            "status": "ready",
            "model_status": "MODELED",
            "operator_api": (
                "bearer-protected" if _auth_required() else "deployment-configured"
            ),
            "public_probes": [
                "/healthz",
                "/readyz",
                "/build-info",
                "/calibration",
                "/master-suite",
            ],
        }
    )


@app.get("/healthz", response_model=ReadinessResponse)
async def healthz() -> ReadinessResponse:
'''

TEST_OLD = '''    with TestClient(api.app) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/readyz").status_code == 200
'''
TEST_NEW = '''    with TestClient(api.app) as client:
        root = client.get("/")
        assert root.status_code == 200
        assert root.json() == {
            "service": "gdw",
            "status": "ready",
            "model_status": "MODELED",
            "operator_api": "bearer-protected",
            "public_probes": [
                "/healthz",
                "/readyz",
                "/build-info",
                "/calibration",
                "/master-suite",
            ],
        }
        assert client.head("/").status_code == 200
        assert client.get("/healthz").status_code == 200
        assert client.get("/readyz").status_code == 200
'''


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected one anchor in {path}, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def govern_metric_cardinality() -> None:
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    for metric_name in ("gdw_request_latency_seconds", "gdw_request_total"):
        allowed = policy["metrics"][metric_name]["allowed_values"]
        routes = allowed["route"]
        methods = allowed["method"]
        if "/" in routes:
            raise RuntimeError(f"{metric_name} already governs the public root")
        routes.insert(0, "/")
        if "HEAD" not in methods:
            methods.insert(methods.index("GET") + 1, "HEAD")
    POLICY.write_text(json.dumps(policy, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    replace_once(API, AUTH_OLD, AUTH_NEW)
    replace_once(API, ROUTE_ANCHOR, ROUTE_BLOCK)
    replace_once(TESTS, TEST_OLD, TEST_NEW)
    govern_metric_cardinality()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
