from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Iterable

import yaml

ROOT = Path(__file__).resolve().parents[1]
DISCLOSURE = (
    "GDW Self-Defending Observability Core is a MODELED operational layer for the Governed Delta Workspace. "
    "It adds structured logging, Prometheus metrics, Grafana/Loki dashboards, alerting, Terraform provisioning, "
    "cardinality audits, and trace-ready hooks. It does not claim universal production-scale guarantees or "
    "frontier benchmark superiority beyond measured outputs and recorded validation artifacts."
)
REQUIRED_PANELS = {
    "Requests/sec",
    "p50 latency",
    "p95 latency",
    "p99 latency",
    "Error ratio",
    "In-flight requests",
    "Decision mix",
    "Receipt integrity",
    "Scheduler mode split",
    "Log-volume spike rate",
    "Live all-logs stream",
    "Error-only stream",
    "Per-endpoint filter",
    "Per-request lookup",
    "Per-session lookup",
}
REQUIRED_ALERTS = {
    "GDWHighP95Latency",
    "GDWHighErrorRate",
    "GDWMissingReceipts",
    "GDWTelemetryAbsent",
}


def run(*args: str) -> None:
    subprocess.run(args, cwd=ROOT, check=True)


def validate_dashboard() -> None:
    path = ROOT / "monitoring" / "grafana" / "dashboards" / "gdw_live_ops_dashboard.json"
    dashboard = json.loads(path.read_text(encoding="utf-8"))
    titles = {str(panel.get("title")) for panel in dashboard.get("panels", [])}
    missing = REQUIRED_PANELS - titles
    if missing:
        raise SystemExit(f"dashboard is missing panels: {sorted(missing)}")
    if dashboard.get("uid") != "gdw-live-ops":
        raise SystemExit("dashboard UID must remain stable")
    serialized = json.dumps(dashboard)
    for datasource_uid in ("gdw-prometheus", "gdw-loki"):
        if datasource_uid not in serialized:
            raise SystemExit(f"dashboard does not reference {datasource_uid}")
    print(f"dashboard valid: {len(titles)} required operator panels")


def validate_alerts() -> None:
    document = yaml.safe_load((ROOT / "monitoring" / "alerting-rules.yml").read_text(encoding="utf-8"))
    rules = document["groups"][0]["rules"]
    names = {rule["alert"] for rule in rules}
    missing = REQUIRED_ALERTS - names
    if missing:
        raise SystemExit(f"alert file is missing rules: {sorted(missing)}")
    for rule in rules:
        expression = str(rule["expr"])
        if rule["alert"] == "GDWHighErrorRate" and "clamp_min" not in expression:
            raise SystemExit("error-rate alert must defend against a zero denominator")
        if "runbook" not in rule.get("annotations", {}):
            raise SystemExit(f"alert has no runbook: {rule['alert']}")
    print(f"alert definitions valid: {len(names)} governed rules")


def validate_truth_labels() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    if DISCLOSURE not in readme:
        raise SystemExit("README is missing the required verbatim MODELED disclosure")
    if "Promtail" in "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in (ROOT / "monitoring").rglob("*")
        if path.is_file()
    ):
        raise SystemExit("Promtail must not be reintroduced; it is removed upstream")
    print("truth labels and collector lifecycle guard valid")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the GDW fail-closed verification gate.")
    parser.add_argument("--with-docker", action="store_true")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run(sys.executable, "ci/audit_cardinality.py")
    run(sys.executable, "ci/update_weight_manifest.py", "--check")
    run(sys.executable, "ci/validate_weights.py")
    validate_dashboard()
    validate_alerts()
    validate_truth_labels()
    if args.with_docker:
        run("docker", "compose", "-f", "monitoring/docker-compose.yml", "config", "--quiet")
    print("GDW gate passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

