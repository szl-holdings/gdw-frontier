from __future__ import annotations

import argparse
import ast
import json
import urllib.request
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from prometheus_client.parser import text_string_to_metric_families

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "app" / "telemetry.py"
DEFAULT_POLICY = ROOT / "monitoring" / "metric-policy.json"
DEFAULT_OUTPUT = ROOT / "output" / "cardinality_audit" / "cardinality_audit.json"
METRIC_CONSTRUCTORS = {"Counter", "Gauge", "Histogram", "Info", "Summary"}


@dataclass(frozen=True)
class MetricDefinition:
    name: str
    labels: tuple[str, ...]
    line: int


def _literal_strings(node: ast.AST | None) -> tuple[str, ...] | None:
    if node is None:
        return ()
    if not isinstance(node, (ast.List, ast.Tuple)):
        return None
    values: list[str] = []
    for item in node.elts:
        if not isinstance(item, ast.Constant) or not isinstance(item.value, str):
            return None
        values.append(item.value)
    return tuple(values)


def extract_metric_definitions(path: Path) -> tuple[list[MetricDefinition], list[dict[str, Any]]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    definitions: list[MetricDefinition] = []
    findings: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        constructor = node.func.id if isinstance(node.func, ast.Name) else None
        if constructor not in METRIC_CONSTRUCTORS:
            continue
        if not node.args or not isinstance(node.args[0], ast.Constant) or not isinstance(node.args[0].value, str):
            findings.append(
                {
                    "severity": "critical",
                    "kind": "dynamic_metric_name",
                    "line": node.lineno,
                    "text": "metric names must be string literals",
                }
            )
            continue
        label_node = node.args[2] if len(node.args) >= 3 else None
        for keyword in node.keywords:
            if keyword.arg in {"labelnames", "labels"}:
                label_node = keyword.value
        labels = _literal_strings(label_node)
        if labels is None:
            findings.append(
                {
                    "severity": "critical",
                    "kind": "dynamic_label_definition",
                    "line": node.lineno,
                    "metric": node.args[0].value,
                    "text": "label names must be a literal list or tuple",
                }
            )
            continue
        definitions.append(MetricDefinition(str(node.args[0].value), labels, node.lineno))
    return definitions, findings


def _canonical_metric(sample_name: str, policy_metrics: dict[str, Any]) -> str | None:
    for metric_name in sorted(policy_metrics, key=len, reverse=True):
        if sample_name == metric_name or sample_name.startswith(f"{metric_name}_"):
            return metric_name
    return None


def audit_static(source: Path, policy: dict[str, Any]) -> tuple[list[dict[str, Any]], list[MetricDefinition]]:
    definitions, findings = extract_metric_definitions(source)
    prefix = str(policy["metric_prefix"])
    policy_metrics: dict[str, Any] = policy["metrics"]
    banned = set(policy["banned_label_tokens"])
    seen: set[str] = set()

    for definition in definitions:
        if not definition.name.startswith(prefix):
            continue
        seen.add(definition.name)
        for label in definition.labels:
            tokens = set(label.lower().replace("-", "_").split("_"))
            if label.lower() in banned or tokens.intersection(banned):
                findings.append(
                    {
                        "severity": "critical",
                        "kind": "banned_label",
                        "line": definition.line,
                        "metric": definition.name,
                        "label": label,
                    }
                )
        expected = policy_metrics.get(definition.name)
        if expected is None:
            findings.append(
                {
                    "severity": "critical",
                    "kind": "unregistered_metric",
                    "line": definition.line,
                    "metric": definition.name,
                }
            )
        elif tuple(expected.get("labels", [])) != definition.labels:
            findings.append(
                {
                    "severity": "critical",
                    "kind": "label_policy_mismatch",
                    "line": definition.line,
                    "metric": definition.name,
                    "actual": list(definition.labels),
                    "expected": expected.get("labels", []),
                }
            )

    for metric_name in sorted(set(policy_metrics) - seen):
        findings.append(
            {
                "severity": "critical",
                "kind": "policy_metric_missing_from_source",
                "metric": metric_name,
            }
        )
    return findings, definitions


def audit_live(
    metrics_text: str,
    policy: dict[str, Any],
    baseline: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    findings: list[dict[str, Any]] = []
    policy_metrics: dict[str, Any] = policy["metrics"]
    series: Counter[str] = Counter()

    for family in text_string_to_metric_families(metrics_text):
        for sample in family.samples:
            canonical = _canonical_metric(sample.name, policy_metrics)
            if canonical is None:
                continue
            series[canonical] += 1
            allowed_values = policy_metrics[canonical].get("allowed_values", {})
            for label_name, allowed in allowed_values.items():
                value = sample.labels.get(label_name)
                if value is not None and value not in allowed:
                    findings.append(
                        {
                            "severity": "critical",
                            "kind": "unexpected_label_value",
                            "metric": canonical,
                            "label": label_name,
                            "value": value,
                        }
                    )

    for metric_name, count in sorted(series.items()):
        budget = int(policy_metrics[metric_name]["max_series"])
        if count > budget:
            findings.append(
                {
                    "severity": "critical",
                    "kind": "metric_series_budget_exceeded",
                    "metric": metric_name,
                    "actual": count,
                    "budget": budget,
                }
            )

    total = sum(series.values())
    if total > int(policy["total_series_budget"]):
        findings.append(
            {
                "severity": "critical",
                "kind": "total_series_budget_exceeded",
                "actual": total,
                "budget": int(policy["total_series_budget"]),
            }
        )

    if baseline:
        ratio = float(baseline.get("max_growth_ratio", 1.5))
        for metric_name, previous in baseline.get("series", {}).items():
            current = series.get(metric_name, 0)
            if int(previous) > 0 and current > int(previous) * ratio:
                findings.append(
                    {
                        "severity": "critical",
                        "kind": "series_growth_exceeded",
                        "metric": metric_name,
                        "actual": current,
                        "baseline": int(previous),
                        "max_growth_ratio": ratio,
                    }
                )
    return findings, dict(sorted(series.items()))


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _fetch_metrics(url: str) -> str:
    request = urllib.request.Request(url, headers={"Accept": "application/openmetrics-text"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.read().decode("utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fail closed on unsafe GDW metric cardinality.")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--metrics-url")
    parser.add_argument("--metrics-file", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    policy = _load_json(args.policy)
    findings, definitions = audit_static(args.source, policy)
    series: dict[str, int] = {}

    metrics_text = None
    if args.metrics_url:
        metrics_text = _fetch_metrics(args.metrics_url)
    elif args.metrics_file:
        metrics_text = args.metrics_file.read_text(encoding="utf-8")
    if metrics_text is not None:
        baseline = _load_json(args.baseline) if args.baseline else None
        live_findings, series = audit_live(metrics_text, policy, baseline)
        findings.extend(live_findings)

    report = {
        "status": "fail" if findings else "pass",
        "source": str(args.source),
        "policy": str(args.policy),
        "static_metric_count": len(definitions),
        "live_series_total": sum(series.values()),
        "live_series": series,
        "findings": findings,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())

