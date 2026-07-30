from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterable

from prometheus_client.parser import text_string_to_metric_families

try:
    from ci.receipt_proof import verify_signed_receipt
except ModuleNotFoundError:
    from receipt_proof import verify_signed_receipt

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COMPOSE_FILE = ROOT / "monitoring" / "docker-compose.yml"
DEFAULT_OUTPUT = ROOT / "output" / "proofs" / "stack-verification.json"


def request_json(
    url: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    basic_auth: tuple[str, str] | None = None,
    bearer_token: str | None = None,
) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if basic_auth:
        token = base64.b64encode(f"{basic_auth[0]}:{basic_auth[1]}".encode()).decode()
        headers["Authorization"] = f"Basic {token}"
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def wait_for(name: str, check: Callable[[], Any], *, timeout: float = 120.0) -> Any:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            result = check()
            print(f"verified: {name}")
            return result
        except (AssertionError, KeyError, OSError, urllib.error.URLError, json.JSONDecodeError) as error:
            last_error = error
            time.sleep(2)
    raise TimeoutError(f"{name} was not verified within {timeout}s: {last_error}")


def prometheus_query(base_url: str, expression: str) -> dict[str, Any]:
    query = urllib.parse.urlencode({"query": expression})
    result = request_json(f"{base_url}/api/v1/query?{query}")
    assert result["status"] == "success"
    assert result["data"]["result"]
    return result


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify the complete local GDW signal path.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--grafana-password", default="gdw-local-admin")
    parser.add_argument(
        "--grafana-password-file",
        type=Path,
        default=os.getenv("GRAFANA_ADMIN_PASSWORD_SOURCE_FILE"),
    )
    parser.add_argument("--api-token", default=os.getenv("GDW_API_TOKEN"))
    parser.add_argument(
        "--api-token-file",
        type=Path,
        default=os.getenv("GDW_API_TOKEN_SOURCE_FILE"),
    )
    parser.add_argument("--app-url", default="http://localhost:8080")
    parser.add_argument("--prometheus-url", default="http://localhost:9090")
    parser.add_argument("--loki-url", default="http://localhost:3100")
    parser.add_argument("--tempo-url", default="http://localhost:3200")
    parser.add_argument("--grafana-url", default="http://localhost:3000")
    args = parser.parse_args(argv)
    if args.grafana_password_file:
        args.grafana_password = args.grafana_password_file.read_text(encoding="utf-8").strip()
    if args.api_token_file:
        args.api_token = args.api_token_file.read_text(encoding="utf-8").strip()
    app_url = args.app_url.rstrip("/")
    prometheus_url = args.prometheus_url.rstrip("/")
    loki_base_url = args.loki_url.rstrip("/")
    tempo_url = args.tempo_url.rstrip("/")
    grafana_url = args.grafana_url.rstrip("/")
    proof: dict[str, Any] = {"status": "UNVERIFIED", "checks": {}}

    wait_for("FastAPI readiness", lambda: _assert_value(request_json(f"{app_url}/readyz"), "status", "ready"))
    wait_for("Prometheus readiness", lambda: _assert_text(f"{prometheus_url}/-/ready", "Ready"))
    wait_for("Loki readiness", lambda: _assert_text(f"{loki_base_url}/ready", "ready"))
    wait_for("Tempo readiness", lambda: _assert_text(f"{tempo_url}/ready", "ready"))
    wait_for("Grafana health", lambda: _assert_value(request_json(f"{grafana_url}/api/health"), "database", "ok"))

    step = request_json(
        f"{app_url}/v1/gdw/step",
        method="POST",
        bearer_token=args.api_token,
        body={
            "session_id": "stack-verification-session",
            "proposal_id": "stack-verification-proposal",
            "objective": "verify metrics logs and traces",
            "confidence": 0.91,
            "receipt_present": True,
            "scheduler_mode": "balanced",
        },
    )
    assert step["status"] == "MODELED"
    assert step["receipt_status"] == "valid"
    assert step["trace_id"]
    request_id = step["request_id"]
    trace_id = step["trace_id"]
    proof["sample"] = {
        "request_id": request_id,
        "trace_id": trace_id,
        "receipt_hash": step["receipt_hash"],
        "status": "MODELED",
    }
    ledger = request_json(
        f"{app_url}/v1/gdw/ledger",
        bearer_token=args.api_token,
    )
    assert ledger["valid"] is True
    assert ledger["signature_status"] == "SIGNED"
    receipt = verify_signed_receipt(
        request_json(
            f"{app_url}/v1/gdw/receipt",
            bearer_token=args.api_token,
        )
    )
    assert receipt["receipt_hash"] == step["receipt_hash"]
    proof["checks"]["cryptographic_receipt"] = receipt

    backup = request_json(
        f"{app_url}/v1/gdw/backup",
        method="POST",
        bearer_token=args.api_token,
    )
    assert backup["status"] == "VERIFIED"
    restore_output = subprocess.check_output(
        [
            "docker",
            "compose",
            "-f",
            str(DEFAULT_COMPOSE_FILE),
            "run",
            "--rm",
            "backup-restore-verify",
        ],
        cwd=ROOT,
        text=True,
    )
    restore = json.loads(restore_output.strip().splitlines()[-1])
    assert restore["status"] == "VERIFIED"
    assert restore["head_hash"] == backup["head_hash"]
    proof["checks"]["backup_restore"] = {
        "backup": backup,
        "restore": restore,
        "scope": "separate local Docker volume restored into isolated tmpfs",
    }

    for index, (confidence, mode) in enumerate(
        [
            (0.95, "interactive"),
            (0.70, "balanced"),
            (0.30, "throughput"),
            (0.96, "interactive"),
            (0.72, "balanced"),
            (0.20, "throughput"),
        ],
        start=1,
    ):
        extra = request_json(
            f"{app_url}/v1/gdw/step",
            method="POST",
            bearer_token=args.api_token,
            body={
                "session_id": f"multiprocess-session-{index}",
                "proposal_id": f"multiprocess-proposal-{index}",
                "objective": "prove metrics aggregate across worker shards",
                "confidence": confidence,
                "receipt_present": True,
                "scheduler_mode": mode,
            },
        )
        assert extra["status"] == "MODELED"

    aggregate = wait_for(
        "Prometheus multiprocess aggregation",
        lambda: _assert_metric_total(
            _fetch_text(f"{app_url}/metrics"),
            "gdw_decision_total",
            7,
        ),
    )
    proof["checks"]["multiprocess_metrics"] = aggregate

    wait_for("Prometheus GDW scrape", lambda: prometheus_query(prometheus_url, "gdw_build_info"))
    targets = wait_for(
        "Prometheus target health",
        lambda: _assert_prometheus_target(request_json(f"{prometheus_url}/api/v1/targets")),
    )
    proof["checks"]["prometheus"] = targets

    rules = wait_for(
        "Prometheus alert evaluation",
        lambda: _assert_prometheus_rules(request_json(f"{prometheus_url}/api/v1/rules?type=alert")),
    )
    proof["checks"]["alerts"] = rules

    dashboard = wait_for(
        "Terraform-provisioned Grafana dashboard",
        lambda: request_json(
            f"{grafana_url}/api/dashboards/uid/gdw-live-ops",
            basic_auth=("admin", args.grafana_password),
        ),
    )
    assert dashboard["dashboard"]["uid"] == "gdw-live-ops"
    proof["checks"]["grafana_dashboard"] = {
        "uid": dashboard["dashboard"]["uid"],
        "title": dashboard["dashboard"]["title"],
        "panel_count": len(dashboard["dashboard"]["panels"]),
    }

    grafana_alerts = wait_for(
        "Terraform-provisioned Grafana alerts",
        lambda: _assert_grafana_alerts(
            request_json(
                f"{grafana_url}/api/v1/provisioning/alert-rules",
                basic_auth=("admin", args.grafana_password),
            )
        ),
    )
    proof["checks"]["grafana_alerts"] = grafana_alerts

    loki_query = '{job="gdw"} | json | request_id="' + request_id + '"'
    loki_url = f"{loki_base_url}/loki/api/v1/query_range?" + urllib.parse.urlencode(
        {"query": loki_query, "limit": 20}
    )
    loki = wait_for("Alloy to Loki log correlation", lambda: _assert_loki_result(request_json(loki_url)))
    proof["checks"]["loki"] = loki

    tempo = wait_for(
        "OTLP to Tempo trace correlation",
        lambda: _assert_tempo_trace(request_json(f"{tempo_url}/api/traces/{trace_id}")),
    )
    proof["checks"]["tempo"] = tempo

    proof["status"] = "VERIFIED"
    proof["scope"] = "local Docker Compose integration only"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(proof, indent=2, sort_keys=True))
    return 0


def _assert_value(value: dict[str, Any], key: str, expected: Any) -> dict[str, Any]:
    assert value[key] == expected
    return value


def _assert_text(url: str, expected: str) -> dict[str, str]:
    text = _fetch_text(url).strip()
    assert expected in text
    return {"body": text}


def _fetch_text(url: str) -> str:
    with urllib.request.urlopen(url, timeout=10) as response:
        return response.read().decode("utf-8")


def _assert_metric_total(exposition: str, sample_name: str, minimum: float) -> dict[str, Any]:
    total = sum(
        float(sample.value)
        for family in text_string_to_metric_families(exposition)
        for sample in family.samples
        if sample.name == sample_name
    )
    assert total >= minimum
    return {"sample": sample_name, "observed_total": total, "minimum": minimum}


def _assert_prometheus_target(payload: dict[str, Any]) -> dict[str, Any]:
    targets = payload["data"]["activeTargets"]
    gdw_targets = [target for target in targets if target["labels"].get("job") == "gdw"]
    assert gdw_targets and all(target["health"] == "up" for target in gdw_targets)
    return {"active_gdw_targets": len(gdw_targets), "health": "up"}


def _assert_prometheus_rules(payload: dict[str, Any]) -> dict[str, Any]:
    rules = [rule for group in payload["data"]["groups"] for rule in group["rules"]]
    names = {rule["name"] for rule in rules}
    required = {"GDWHighP95Latency", "GDWHighErrorRate", "GDWMissingReceipts", "GDWTelemetryAbsent"}
    assert required.issubset(names)
    assert all(rule.get("health") == "ok" for rule in rules if rule["name"] in required)
    return {"loaded": sorted(required), "health": "ok"}


def _assert_grafana_alerts(payload: Any) -> dict[str, Any]:
    rules = payload if isinstance(payload, list) else payload.get("items", [])
    names = {rule.get("title") for rule in rules}
    required = {"GDWHighP95Latency", "GDWHighErrorRate", "GDWMissingReceipts", "GDWTelemetryAbsent"}
    assert required.issubset(names)
    return {"loaded": sorted(required)}


def _assert_loki_result(payload: dict[str, Any]) -> dict[str, Any]:
    result = payload["data"]["result"]
    assert result
    return {"streams": len(result), "status": "ingested"}


def _assert_tempo_trace(payload: dict[str, Any]) -> dict[str, Any]:
    batches = payload.get("batches", [])
    assert batches
    span_count = sum(
        len(scope.get("spans", []))
        for batch in batches
        for scope in batch.get("scopeSpans", batch.get("instrumentationLibrarySpans", []))
    )
    assert span_count > 0
    return {"batches": len(batches), "span_count": span_count, "status": "ingested"}


if __name__ == "__main__":
    raise SystemExit(main())
