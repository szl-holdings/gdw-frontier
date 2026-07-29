from __future__ import annotations

import json
from pathlib import Path

from ci.audit_cardinality import audit_live, audit_static, extract_metric_definitions

ROOT = Path(__file__).resolve().parents[1]


def test_current_metric_definitions_match_policy() -> None:
    policy = json.loads((ROOT / "monitoring" / "metric-policy.json").read_text(encoding="utf-8"))
    findings, definitions = audit_static(ROOT / "app" / "telemetry.py", policy)
    assert findings == []
    assert len(definitions) == len(policy["metrics"])


def test_ast_audit_finds_banned_label(tmp_path: Path) -> None:
    source = tmp_path / "telemetry.py"
    source.write_text(
        'from prometheus_client import Counter\nBAD = Counter("gdw_bad_total", "bad", ["request_id"])\n',
        encoding="utf-8",
    )
    definitions, findings = extract_metric_definitions(source)
    assert findings == []
    assert definitions[0].labels == ("request_id",)

    policy = {
        "metric_prefix": "gdw_",
        "banned_label_tokens": ["request_id"],
        "metrics": {"gdw_bad_total": {"labels": ["request_id"], "max_series": 1}},
    }
    findings, _ = audit_static(source, policy)
    assert any(finding["kind"] == "banned_label" for finding in findings)


def test_live_audit_enforces_allowed_values_and_budget() -> None:
    policy = {
        "metrics": {
            "gdw_decision_total": {
                "max_series": 1,
                "allowed_values": {"decision": ["allow"]},
            }
        },
        "total_series_budget": 1,
    }
    exposition = """# TYPE gdw_decision_total counter
gdw_decision_total{decision="allow"} 1
gdw_decision_total{decision="user-123"} 1
"""
    findings, series = audit_live(exposition, policy, None)
    assert series["gdw_decision_total"] == 2
    kinds = {finding["kind"] for finding in findings}
    assert "unexpected_label_value" in kinds
    assert "metric_series_budget_exceeded" in kinds
    assert "total_series_budget_exceeded" in kinds

